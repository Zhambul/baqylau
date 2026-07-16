# plugins/claude_code/file_fmt.py — PostToolUse file-op formatter
# Entry point: claude-file-fmt.py (a thin shim — the entry FILENAME is the audit vocabulary).
# claude-file-fmt.py — formatter for file-operation lines in the kitty mirror.
#
# Reads a Claude Code PostToolUse payload (JSON) on stdin for a Read/Edit/Write/
# MultiEdit/NotebookEdit tool and appends a compact one-liner to the mirror log
# (argv[1]) showing just the verb + file basename, e.g.
#
#   Read(README.md)
#   Update(claude-cmd-fmt.py)
#   Write(new_thing.py)
#
# Invoked directly as the PostToolUse hook. Verbs mirror Claude Code's own UI: Edit and
# MultiEdit show as "Update", Write as "Write", Read as "Read".
#
# CLICK-TO-VIEW: the one-liner itself is an OSC 8 hyperlink
# (claude-copy:///<key>/<tool_use_id>/view). The full content — the text a Read
# returned, a Write's body (both syntax-highlighted, dim line numbers), a
# Claude-Code-style ± diff for Update — is pre-rendered HERE at hook time
# (width-independent work: highlight, diff styling; wrapping stays the
# renderer's) into a list of paint ops stashed in the state DB's kv table under
# `view:<tool_use_id>`, and the emitted line op carries the id as "v". Clicking
# the line runs claude-copy.py (open-actions.conf), which TOGGLES the id in the
# session's `view-open` kv set; the renderer paints the stashed block INLINE
# under the line while its id is open (a full reflow repaint per toggle — the
# resize path), so the block expands in place and a second click hides it.
# Pre-rendering at hook time is what makes the click work FOREVER: the payload
# (tool_response content, old/new strings) exists only while this hook runs,
# and the file on disk drifts — the kv stash is parked/restored with the
# session like the ops history itself.
import os
from urllib.parse import quote

from core import ops as O
from core import paths as PATHS
from core import render as R
from core import state as S
from core import streamfmt as SF
from plugins.claude_code import hookkit as H
from plugins.claude_code import tools as CT

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

# Verbs + colours are the shared core.ops table (claude-substream.py renders a
# subagent's file ops with the SAME verbs/colours).
LABEL = CT.FILE_LABEL


# SGR primitives are core.render's (R.fg/R.DIM/R.RST — the shared One Dark
# palette); DEF is its default-foreground entry, aliased for the diff fallback.
DEF = R.COL["def"]

# Click-to-view diff panel tints — the git/delta-style SOFT row backgrounds;
# the fg stays the shared semantic GREEN/RED; the tint is what reads "diff".
# The view body is deliberately UNCAPPED (owner's call): the stash lives in
# SQLite and the pane scrolls — truncating the very content the click asked for
# would defeat the feature (a bare Read already caps itself at 2000 lines).
ADD_BG = (36, 52, 40)     # soft green panel behind '+' rows
DEL_BG = (62, 34, 38)     # soft red panel behind '-' rows


def _read_text(path, ti, tr):
    """(text, first_line_number) a Read actually returned: the result's file
    content when the payload carries it, else the file re-read from disk at hook
    time sliced to the input's offset/limit (close enough — the hook runs
    immediately after the tool). (None, 1) when unreadable."""
    finfo = tr.get("file") if isinstance(tr, dict) else None
    if isinstance(finfo, dict) and isinstance(finfo.get("content"), str):
        return finfo["content"], int(finfo.get("startLine") or 1)
    try:
        with open(path, "r", errors="replace") as f:
            lines = f.read().split("\n")
    except OSError:
        return None, 1
    off = int(ti.get("offset") or 1)
    lim = ti.get("limit")
    if off > 1 or lim:
        end = off - 1 + int(lim) if lim else len(lines)
        lines = lines[off - 1:end]
    return "\n".join(lines), off


def _lexer(path):
    """The pygments lexer for a path's extension (python/kotlin/java/bash and
    friends — the shared coderender.LANGS table), or None."""
    from core.coderender import LANGS
    return LANGS.get(os.path.splitext(path)[1].lower())


def _code_ops(path, text, start, rgb):
    """One gut op carrying the RAW body plus the paint-time spec: 'lex' (the
    extension-picked pygments lexer) and 'num' (first line number).
    Highlighting and numbering happen in the RENDERER, the one process
    guaranteed a pygments (hook producers may run a bare python3 — the same
    reason `code` ops ship raw text). Uncapped — the whole extent the op
    touched is what a click asks for."""
    return [O.gut(text.rstrip("\n"), rgb, lex=_lexer(path), num=start)]


def _md_ops(text, rgb):
    """gut ops for a markdown Read/Write body, pretty-rendered by the SAME
    AST renderer the live streaming path uses (core.mdrender.MarkdownStreamer):
    headings→amber banners, bold/emphasis, lists, blockquotes, GFM tables, and
    fenced code blocks in their own CODE_BG panel. Each `(text, bg)` segment
    becomes one already-styled gut op (mirrors stream.py's emit_md) — no
    'lex'/'num', so it paints verbatim (no line-number gutter; prose isn't
    source). mdrender degrades gracefully when wenmode/pygments are absent
    (this hook may run a bare python3), exactly as the streaming path relies on.
    Falls back to a plain code op if rendering yields nothing."""
    from core import mdrender as MDR
    stream = MDR.MarkdownStreamer()
    segs = stream.feed(text) + stream.close()
    ops = [O.gut(t.rstrip("\n"), rgb, bg=bg) for t, bg in segs
           if t.strip() or bg is not None]
    return ops


def _diff_ops(rows, rgb, lexer):
    """gut ops for diff rows (CT.diff_rows), delta-style: contiguous same-signed
    runs share one op, every row keeps a dim line-number gutter (the OLD number
    for a removal, the NEW one for an addition/context — no +/- signs), and
    when the file's extension maps to a lexer the run ships RAW with a
    'lex'/'num' spec so the RENDERER syntax-highlights the code at paint time
    (same deferral as Read/Write bodies) — the soft red/green panel ('bg')
    alone carries the removal/addition meaning. Without a lexer the run falls
    back to red/green foreground text. Hunk separators paint a dim ⋮. A run's
    numbers are sequential by construction (diff_rows walks each hunk in
    order), which is what lets 'num' number the whole run from its first row."""
    ops = []
    buf, cur_sign, cur_start = [], None, None

    def flush():
        nonlocal buf
        if not buf:
            return
        bg = ADD_BG if cur_sign == "+" else DEL_BG if cur_sign == "-" else None
        if lexer:
            ops.append(O.gut("\n".join(buf), rgb, bg=bg, lex=lexer,
                             num=cur_start))
        else:
            col = (R.fg(*O.GREEN) if cur_sign == "+" else
                   R.fg(*O.RED) if cur_sign == "-" else DEF)
            body = "\n".join(
                R.DIM + ("%5d " % (cur_start + i) if cur_start is not None
                         else " " * 6) + R.RST + col + t + R.RST
                for i, t in enumerate(buf))
            ops.append(O.gut(body, rgb, bg=bg) if bg else O.gut(body, rgb))
        buf = []

    for sign, no, text in rows:
        if sign == "@":
            flush()
            cur_sign = None
            ops.append(O.gut(R.DIM + "    ⋮" + R.RST, rgb))
            continue
        if sign != cur_sign:
            flush()
            cur_sign, cur_start = sign, no
        buf.append(text)
    flush()
    return ops


def view_ops(tool, label, name, path, ti, tr):
    """The click-to-view block for one file op, as a list of paint-op dicts
    (JSON-clean — exactly what claude-copy.py O.emit()s on a /view click), or
    None when there is nothing to show (empty content, unreadable file).

    Public API: the subagent substream renderer
    (plugins/claude_code/substream_render.py) builds its file-op view stashes
    through this too — file_fmt owns the block builder for both, so a Read/
    Write body and an Update diff render identically whether the main session
    or a subagent did the op. When the caller's tool_result lacks the Read
    content/structuredPatch (a subagent transcript's usually does), the
    builders below fall back to the disk re-read / input-strings difflib."""
    rgb = CT.FILE_RGB.get(label, O.SLATE)
    if tool == "Read":
        text, start = _read_text(path, ti, tr)
        if text is None or not text.strip():
            return None
        body = _md_ops(text, rgb) if CT.is_md(path) else _code_ops(path, text, start, rgb)
        suffix = CT.read_extent(tr.get("file") if isinstance(tr, dict) else None, ti)
    elif tool == "Write":
        text = ti.get("content") or ""
        if not text.strip():
            return None
        body = _md_ops(text, rgb) if CT.is_md(path) else _code_ops(path, text, 1, rgb)
        suffix = "+%d" % len(text.splitlines())
    else:
        rows = CT.diff_rows(tool, ti, tr)
        if not rows:
            return None
        body = _diff_ops(rows, rgb, _lexer(path))
        a, r = CT.diff_counts(tool, ti)
        suffix = " ".join(p for p in (("+%d" % a) if a else "",
                                      ("-%d" % r) if r else "") if p)
    hdr = label + " " + name + ((" · " + suffix) if suffix else "")
    return [O.rule(), O.label(hdr, rgb)] + body + [O.blank()]


def stash_view(log, tid, tool, label, name, path, ti, tr, line,
               who="render", extra=None):
    """The stash-and-link half of click-to-view, shared by this formatter and
    the subagent substream renderer (substream_render.render_file): build the
    view block (view_ops above), park it in the state DB kv table under
    `view:<tid>`, wrap `line` in the claude-copy:///<sid>/<tid>/view OSC 8
    hyperlink, and audit the stash as a `view-stash` state_files row.

    Returns (line, vid): the (possibly hyperlinked) line plus the id to tag the
    emitted op with ("v"), or (line-unchanged, None) when there is nothing to
    show or the stash failed — the caller keeps the plain unlinked line.
    `who` names the caller in the render-failure error row; `extra` merges
    extra context (e.g. the subagent's name) into the audit row."""
    try:
        vops = view_ops(tool, label, name, path, ti, tr)
    except Exception:
        vops = None
        A.error(log, "view-stash (%s)" % who, {"tool": tool, "gid": tid})
    if not (vops and S.kv_set(log, "view:" + str(tid), vops)):
        return line, None
    url = "claude-copy:///%s/%s/view" % (
        quote(PATHS.sid_from_log(log), safe=""), quote(str(tid), safe=""))
    info = {"gid": tid, "tool": tool, "ops": len(vops)}
    if extra:
        info.update(extra)
    A.state_file(log, S.db_path(log), "view-stash", info)
    return R.hyperlink(url, line), tid


def main():
    d, LOG = H.read_payload()
    if d is None:
        return
    # A subagent's file ops are rendered by claude-substream.py (in transcript
    # order, with the subagent's messages) — skip them here to avoid duplication.
    if d.get("agent_id"):
        return H.ignore(d, "agent_id (substream owns rendering)")
    label = LABEL.get(d.get("tool_name") or "")
    if not label:
        return H.ignore(d, "not a file tool")
    ti = d.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if not path:
        return H.ignore(d, "no file path")
    name = os.path.basename(path.rstrip("/")) or path
    failed = H.is_failure(d)
    mark = (R.DIM + " ✗" + R.RST) if failed else ""           # ✗ on failure (verb goes red)
    tool = d.get("tool_name") or ""
    tr = d.get("tool_response")
    added = removed = 0
    ext = rng = ""
    if not failed:
        if tool == "Read":
            # How much of the file it actually read ('' when the whole file). The result
            # carries startLine/numLines/totalLines; tool_input offset/limit is a fallback.
            ext = CT.read_extent(tr.get("file") if isinstance(tr, dict) else None, ti)
        else:
            # Added/removed line counts for a mutation (Read returns (0, 0) → no suffix),
            # then the line range(s) it touched (from the result's structuredPatch).
            added, removed = CT.diff_counts(tool, ti)
            rng = CT.edit_range(tr.get("structuredPatch") if isinstance(tr, dict) else None)
    # The one-liner shape itself is the shared core builder (streamfmt.file_line
    # — the substream and codex renderers paint the same anatomy); the failure
    # mark stays this formatter's own. The displayed name carries the location
    # (streamfmt.file_display): bare basename under the session cwd, ✎ for a
    # scratchpad file, dim abbreviated dir for anything else outside the project.
    disp, loc = SF.file_display(path, d.get("cwd"))
    line = SF.file_line(label, disp, CT.FILE_RGB.get(label, O.SLATE),
                        failed=failed, extent=ext,
                        added=added, removed=removed, rng=rng) + mark
    # Click-to-view: stash the pre-rendered content block under the op's
    # tool_use_id, wrap the WHOLE one-liner in the claude-copy:///…/view
    # hyperlink (a `line` op paints verbatim, so the producer bakes the link;
    # after a sid-fork adoption the old key still resolves through the symlink
    # adopt.py leaves at the old DB path), and tag the op with the id ("v") so
    # the renderer knows where to expand the block in place. A failed op or a
    # stash that came up empty keeps the plain unlinked line.
    vid = None
    gid = d.get("tool_use_id") or None
    if not failed and gid:
        line, vid = stash_view(LOG, gid, tool, label, name, path, ti, tr, line)
    viewed = vid is not None
    O.emit(LOG, O.line(line, view=vid))
    # Feed the session scoreboard (best-effort): the touched path (files counts
    # UNIQUE files — see bump()) plus the mutation's +/- line counts, keyed by the
    # raw tool name (Read/Edit/Write/MultiEdit/NotebookEdit) for the tools breakdown.
    # Token/cost spend is no longer folded here — the OTLP receiver (plugins/otel/)
    # owns it and updates the scoreboard live.
    O.bump(LOG, tool=tool, file=path, added=added, removed=removed)
    A.hook_event(d, decision=f"rendered: {label}({name})"
                 + (f" [{loc}]" if loc else "")
                 + (" FAILED" if failed else
                    ("" if tool == "Read" else f" +{added} -{removed}"))
                 + (" +view" if viewed else ""))


def entry():
    H.run(main)
