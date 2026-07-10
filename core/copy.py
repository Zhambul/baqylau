# core/copy.py — the mirror's ⧉ copy-link handler.
# Entry point: claude-copy.py (a thin shim — the entry FILENAME is the audit vocabulary).
#
# The renderer (claude-mirror.py) paints " ⧉cmd ⧉out" on every group-tagged label
# op, wrapped in an OSC 8 hyperlink with the custom scheme
#
#     claude-copy:///<key>/<gid>/<what>          what: cmd | out
#
# kitty resolves a plain left-click on such a link through
# ~/.config/kitty/open-actions.conf, whose `protocol claude-copy` rule launches
# this handler (detached, --type=background) with the URL. <key> is the mirror-log
# key (claude_paths.sid_from_log); <gid> is the block's copy-group id — the Bash
# tool_use_id, or the backgroundTaskId for a background job — stamped as "g" on
# the block's ops by the producers (claude-cmd-pre.py / claude-cmd-fmt.py /
# claude-stream.py).
#
# The handler re-reads the group's ops from the per-session state DB — READ-ONLY
# (mode=ro), so a click after SessionEnd can never recreate a DB whose
# file-existence is the session-alive signal — takes `code` ops for "cmd"
# (preferring the "raw" field: the exact command that ran, before pretty-printing)
# and ANSI-stripped `gut` ops for "out", pipes the text to the OS clipboard
# (pbcopy / wl-copy / xclip / xsel; CLAUDE_COPY_CMD overrides — the test seam),
# and appends a one-line feedback op to the mirror so the click visibly landed.
# Copying from the ops table (not the transient .out tee files) is what makes
# scrolled-back and even resumed-session blocks copyable: the DB holds the whole
# history and is parked/restored across resume.
#
# Every path audits: success/empty as a state_files row (action "copy"), every
# failure as an errors row — a click that "did nothing" is answerable from the DB.
import json
import os
import sqlite3
import subprocess
from urllib.parse import unquote

from core import paths as P

try:
    from core import audit as A
except Exception:                       # audit must never break the handler
    class _NoAudit:
        def __getattr__(self, _):
            return lambda *a, **k: None
    A = _NoAudit()


def parse_url(url):
    """claude-copy:///<key>/<gid>/<what> -> (key, gid, what), or None."""
    scheme, sep, rest = (url or "").partition("://")
    if not sep or scheme != "claude-copy":
        return None
    parts = [unquote(p) for p in rest.strip("/").split("/")]
    if len(parts) != 3 or parts[2] not in ("cmd", "out") or not all(parts):
        return None
    return tuple(parts)


def collect(db, gid, what):
    """The copy text for a group: its `code` ops ("cmd") or the visible text of
    its `gut` ops ("out"), in insertion order. Opens the state DB mode=ro — this
    handler must never create runtime state (see module docstring)."""
    from core import render as R
    conn = sqlite3.connect("file:%s?mode=ro" % db, uri=True, timeout=2.0)
    try:
        rows = conn.execute("SELECT op FROM ops ORDER BY id").fetchall()
    finally:
        conn.close()
    out = []
    for (s,) in rows:
        try:
            op = json.loads(s)
        except Exception:
            continue
        if op.get("g") != gid:
            continue
        t = op.get("t")
        if what == "cmd" and t == "code":
            out.append(op.get("raw") or op.get("s") or "")
        elif what == "out" and t == "gut":
            out.append(R.strip_ansi(op.get("s") or ""))
    return "\n".join(out)


def to_clipboard(text):
    """Pipe `text` to the OS clipboard. CLAUDE_COPY_CMD (a shell command reading
    stdin) overrides the probe — the test seam. True on success."""
    override = os.environ.get("CLAUDE_COPY_CMD")
    cands = ([["/bin/sh", "-c", override]] if override else
             [["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"],
              ["xsel", "--clipboard", "--input"]])
    for argv in cands:
        try:
            subprocess.run(argv, input=text.encode("utf-8", "replace"),
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, check=True)
            return True
        except Exception:
            continue
    return False


def _feedback(log, text):
    from core import ops as O
    from core import render as R
    O.emit(log, O.line(R.DIM + "  ⧉ " + text + R.RST))


def main(argv):
    parsed = parse_url(argv[1] if len(argv) > 1 else "")
    if not parsed:
        A.error("", "copy (bad url)", {"argv": argv[1:2]})
        return
    key, gid, what = parsed
    log = P.PREFIX + key + ".log"
    db = P.state_db(log)
    if not os.path.exists(db):
        # Session over (DB parked away at SessionEnd) — nothing to read, and
        # touching the path would fake a session-alive signal. Silently a no-op
        # on screen (the pane's renderer is gone too), but audited.
        A.error(log, "copy (state DB gone — session over?)",
                {"gid": gid, "what": what})
        return
    try:
        text = collect(db, gid, what)
    except Exception:
        A.error(log, "copy (read ops)", {"gid": gid, "what": what})
        return
    if not text.strip():
        _feedback(log, "nothing to copy (%s)" % what)
        A.state_file(log, db, "copy", {"gid": gid, "what": what, "chars": 0})
        return
    if to_clipboard(text):
        _feedback(log, "copied %s · %d chars"
                  % ("command" if what == "cmd" else "output", len(text)))
        A.state_file(log, db, "copy",
                     {"gid": gid, "what": what, "chars": len(text)})
    else:
        _feedback(log, "copy failed — no clipboard tool")
        A.error(log, "copy (no clipboard tool)", {"gid": gid, "what": what})


def entry(argv):
    try:
        main(argv)
    except Exception:
        try:
            A.error("", "claude-copy entry")
        except Exception:
            pass
