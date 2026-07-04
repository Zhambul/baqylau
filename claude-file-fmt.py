#!/usr/bin/env python3
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
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_ops as O

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

LABEL = {
    "Read": "Read",
    "Edit": "Update",
    "MultiEdit": "Update",
    "Write": "Write",
    "NotebookEdit": "Update",
}


def fg(r, g, b):
    return f"\033[38;2;{r};{g};{b}m"


COLOR = {
    "Read":   fg(97, 175, 239),   # blue   — a read
    "Update": fg(229, 192, 123),  # yellow — a modification
    "Write":  fg(152, 195, 121),  # green  — a new file written
}
DIM = fg(92, 99, 112)
DEF = fg(171, 178, 191)
RST = "\033[0m"


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        A.error("", "payload parse (stdin not valid JSON)")
        return
    # A subagent's file ops are rendered by claude-substream.py (in transcript
    # order, with the subagent's messages) — skip them here to avoid duplication.
    if d.get("agent_id"):
        A.hook_event(d, decision="ignored: agent_id (substream owns rendering)")
        return
    label = LABEL.get(d.get("tool_name") or "")
    if not label:
        A.hook_event(d, decision="ignored: not a file tool")
        return
    ti = d.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if not path:
        A.hook_event(d, decision="ignored: no file path")
        return
    LOG = O.log_path(d)
    name = os.path.basename(path.rstrip("/")) or path
    failed = "Failure" in (d.get("hook_event_name") or "")
    if failed:
        col, mark = fg(224, 108, 117), DIM + " ✗" + RST   # red verb + ✗ on failure
    else:
        col, mark = COLOR.get(label, DEF), ""
    tool = d.get("tool_name") or ""
    tr = d.get("tool_response")
    added = removed = 0
    line = col + label + DIM + "(" + DEF + name + DIM + ")" + RST
    if not failed:
        if tool == "Read":
            # How much of the file it actually read ('' when the whole file). The result
            # carries startLine/numLines/totalLines; tool_input offset/limit is a fallback.
            finfo = tr.get("file") if isinstance(tr, dict) else None
            ext = O.read_extent(finfo, ti)
            if ext:
                line += "  " + DIM + ext + RST
        else:
            # Added/removed line counts for a mutation (Read returns (0, 0) → no suffix),
            # then the line range(s) it touched (from the result's structuredPatch).
            added, removed = O.diff_counts(tool, ti)
            parts = []
            if added:
                parts.append(fg(152, 195, 121) + f"+{added}" + RST)   # green additions
            if removed:
                parts.append(fg(224, 108, 117) + f"-{removed}" + RST)  # red removals
            if parts:
                line += "  " + " ".join(parts)
            rng = O.edit_range(tr.get("structuredPatch") if isinstance(tr, dict) else None)
            if rng:
                line += "  " + DIM + rng + RST
    line += mark
    O.emit(LOG, O.line(line))
    # Feed the session scoreboard (best-effort): the touched path (files counts
    # UNIQUE files — see bump()) plus the mutation's +/- line counts, keyed by the
    # raw tool name (Read/Edit/Write/MultiEdit/NotebookEdit) for the tools breakdown,
    # then the main session's own token spend since the last hook (see
    # claude_ops.bump_transcript).
    O.bump(LOG, tool=tool, file=path, added=added, removed=removed)
    O.bump_transcript(LOG, d.get("transcript_path"))
    A.hook_event(d, decision=f"rendered: {label}({name})"
                 + (" FAILED" if failed else
                    ("" if tool == "Read" else f" +{added} -{removed}")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        A.error("", "main")
