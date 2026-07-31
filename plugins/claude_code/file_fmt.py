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

from core import copy as C
from core import ops as O
from core import render as R
from core import streamfmt as SF
from plugins.claude_code import hookkit as H
from plugins.claude_code import fileobs as FOBS
from plugins.claude_code import tools as CT

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

# Verbs + colours are the shared core.ops table (claude-substream.py renders a
# subagent's file ops with the SAME verbs/colours).
LABEL = CT.FILE_LABEL


def _read_text(path, ti, tr):
    """(text, first_line_number) a Read actually returned: the result's file
    content when the payload carries it, else the file re-read from disk at hook
    time sliced to the input's offset/limit (close enough — the hook runs
    immediately after the tool). (None, 1) when unreadable."""
    finfo = tr.get("file") if isinstance(tr, dict) else None
    if isinstance(finfo, dict) and isinstance(finfo.get("content"), str):
        return finfo["content"], int(finfo.get("startLine") or 1)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().split("\n")
    except OSError:
        return None, 1
    off = int(ti.get("offset") or 1)
    lim = ti.get("limit")
    if off > 1 or lim:
        end = off - 1 + int(lim) if lim else len(lines)
        lines = lines[off - 1:end]
    return "\n".join(lines), off


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
        body = SF.file_md_ops(text, rgb) if SF.file_is_md(path) \
            else SF.file_code_ops(path, text, start, rgb)
        suffix = CT.read_extent(tr.get("file") if isinstance(tr, dict) else None, ti)
    elif tool == "Write":
        text = ti.get("content") or ""
        if not text.strip():
            return None
        body = SF.file_md_ops(text, rgb) if SF.file_is_md(path) \
            else SF.file_code_ops(path, text, 1, rgb)
        suffix = "+%d" % len(text.splitlines())
    else:
        rows = CT.diff_rows(tool, ti, tr)
        if not rows:
            return None
        body = SF.file_diff_ops(rows, path, rgb)
        a, r = CT.diff_counts(tool, ti)
        suffix = " ".join(p for p in (("+%d" % a) if a else "",
                                      ("-%d" % r) if r else "") if p)
    return SF.file_view_ops(label, name, rgb, body, suffix)


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
    extra context (e.g. the subagent's name) into the audit row.

    What is FILE-op-specific is building the block (view_ops above); parking it,
    linking the line and auditing the stash is the click-to-view protocol itself
    and belongs to its owner (core.copy.stash — the toggle's other half)."""
    try:
        vops = view_ops(tool, label, name, path, ti, tr)
    except Exception:
        vops = None
        A.error(log, "view-stash (%s)" % who, {"tool": tool, "gid": tid})
    info = {"tool": tool}
    if extra:
        info.update(extra)
    return C.stash(log, tid, vops, line, info)


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
    # File-op OBSERVERS (fileobs.OBSERVERS — the memory wiki is the one row):
    # each match bakes its marker glyph into the one-liner before the emit; the
    # kv snapshot (record()) happens AFTER the emit, so the first op's own emit
    # has created the state DB the parked-guarded record() needs (main agent —
    # agent_id was bailed above; the substream records subagent ops the same way).
    # The `mem=` op flag stays MEMORY's vocabulary, not the registry's: it is a
    # one-member web classification hint (core/ops.py), keyed off the row name.
    obs = FOBS.matches(path, d.get("cwd") or "")
    is_mem = any(o.key == "memory" for o in obs)
    for o in obs:
        line += "  " + R.DIM + o.mark + R.RST
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
    # `act` is the display VERB's class, through the one owner of that mapping
    # (streamfmt.file_act — the same table the web's parked-history fallback
    # matches these verbs with).
    O.emit(LOG, O.line(line, view=vid, mem=is_mem, act=SF.file_act(label),
                       add=0 if failed else added,
                       rem=0 if failed else removed))
    # Now that the emit has ensured the state DB exists, each matched observer
    # snapshots the touched file into its kv (notes = the audit fragments, empty
    # when nothing recorded / the DB is somehow still parked).
    notes = [n for n in (o.record(LOG, path, label, agent=None) for o in obs) if n]
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
                 + (" +view" if viewed else "")
                 + ("".join(f" +{n}" for n in notes) if notes
                    else (" +mem" if is_mem else "")))


def entry():
    H.run(main)
