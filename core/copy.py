# core/copy.py — the mirror's ⧉ copy-link handler.
# Entry point: claude-copy.py (a thin shim — the entry FILENAME is the audit vocabulary).
#
# The renderer (claude-mirror.py) paints " ⧉cmd ⧉out" on every group-tagged label
# op, wrapped in an OSC 8 hyperlink with the custom scheme
#
#     claude-copy:///<key>/<gid>/<what>          what: cmd | out | all
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
# file-existence is the session-alive signal — takes `code` ops for "cmd" (the
# text AS DISPLAYED, i.e. the pretty-printed form — WYSIWYG, owner's call; it is
# equivalent runnable bash) and ANSI-stripped `gut` ops for "out", pipes the
# text to the OS clipboard
# (pbcopy / wl-copy / xclip / xsel; CLAUDE_COPY_CMD overrides — the test seam),
# and appends a one-line feedback op to the mirror so the click visibly landed.
# Copying from the ops table (not the transient .out tee files) is what makes
# scrolled-back and even resumed-session blocks copyable: the DB holds the whole
# history and is parked/restored across resume.
#
# A fourth verb, "view" (a file-op one-liner click — Read/Update/Write lines,
# whose hyperlink file_fmt.py bakes into the line op itself), doesn't copy:
# it TOGGLES the block's id in the session's `view-open` kv set, and the
# renderer expands/collapses the kv-stashed pre-rendered content block
# (`view:<gid>`) in place under the line. The toggle is a state-DB write, which
# is fine under the same exists-guard the feedback op already relies on — only
# the no-DB path must stay read-only.
#
# Every path audits: success/empty as a state_files row (action "copy"/"view"),
# every failure as an errors row — a click that "did nothing" is answerable
# from the DB.
import json
import os
import signal
import sqlite3
import subprocess
from urllib.parse import unquote

from core import paths as P

from core.noaudit import load_audit

A = load_audit()   # always-on audit trail (CLAUDE_AUDIT=0 disables); inert stub if it can't import


def parse_url(url):
    """claude-copy:///<key>/<gid>/<what> -> (key, gid, what), or None.
    what: cmd/out/all copy to the clipboard; view toggles a file-op block's
    in-place expansion."""
    scheme, sep, rest = (url or "").partition("://")
    if not sep or scheme != "claude-copy":
        return None
    parts = [unquote(p) for p in rest.strip("/").split("/")]
    if (len(parts) != 3 or parts[2] not in ("cmd", "out", "all", "view")
            or not all(parts)):
        return None
    return tuple(parts)


def toggle_view(log, db, gid):
    """Flip `gid` in the session's `view-open` kv set (the renderer expands the
    kv-stashed `view:<gid>` block in place while its id is in the set). No-op
    with feedback when there is no stash for the id (a pre-feature line, or the
    stash write failed — the line then carries no hyperlink anyway, so this is
    belt-and-braces)."""
    from core import state as S
    # Existence check only — do NOT kv_get the stash: an uncapped Read view
    # can be megabytes, and json-parsing it here just to test truthiness was
    # a visible chunk of the click latency.
    conn = S.connect(log)
    row = conn.execute("SELECT 1 FROM kv WHERE key=?",
                       ("view:" + gid,)).fetchone() if conn is not None else None
    if not row:
        _feedback(log, "nothing to show")
        A.state_file(log, db, "view", {"gid": gid, "open": None, "ops": 0})
        return
    cur = set(S.kv_get(log, "view-open") or [])
    opened = gid not in cur
    cur = cur | {gid} if opened else cur - {gid}
    if S.kv_set(log, "view-open", sorted(cur)):
        # Nudge the renderer for an INSTANT reflow — without this the expansion
        # waits out the renderer's 200ms poll tick, which reads as lag on a
        # click. SIGWINCH is its existing "reflow now" signal; the pid is the
        # kv row the renderer registers at startup. Best-effort: a dead/absent
        # pid just falls back to the poll. Kill BEFORE the audit write — the
        # nudge is the user-visible part of the click.
        pid = S.kv_get(log, "renderer-pid")
        if pid:
            try:
                os.kill(int(pid), signal.SIGWINCH)
            except Exception:
                pass
        A.state_file(log, db, "view", {"gid": gid, "open": opened})
    else:
        A.error(log, "view (toggle write)", {"gid": gid})


def collect(db, gid, what):
    """The copy text for a group, in insertion order: its `code` ops ("cmd"), the
    visible text of its `gut` ops ("out"), or BOTH interleaved ("all" — the whole
    block, for a body-only activity like a message/prompt/result/file op). Opens the
    state DB mode=ro — this handler must never create runtime state (see docstring)."""
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
        if t == "code" and what in ("cmd", "all"):
            out.append(op.get("s") or "")
        elif t == "gut" and what in ("out", "all"):
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
    log = P.log_for_key(key)
    db = P.state_db(log)
    if not os.path.exists(db):
        # Session over (DB parked away at SessionEnd) — nothing to read, and
        # touching the path would fake a session-alive signal. Silently a no-op
        # on screen (the pane's renderer is gone too), but audited.
        A.error(log, "copy (state DB gone — session over?)",
                {"gid": gid, "what": what})
        return
    if what == "view":
        try:
            toggle_view(log, db, gid)
        except Exception:
            A.error(log, "view (toggle)", {"gid": gid})
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
                  % ({"cmd": "command", "out": "output"}.get(what, "block"),
                     len(text)))
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
