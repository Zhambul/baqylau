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
# Invoked by claude-file-log.sh. Verbs mirror Claude Code's own UI: Edit and
# MultiEdit show as "Update", Write as "Write", Read as "Read".
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_ops as O

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
        return
    # A subagent's file ops are rendered by claude-substream.py (in transcript
    # order, with the subagent's messages) — skip them here to avoid duplication.
    if d.get("agent_id"):
        return
    label = LABEL.get(d.get("tool_name") or "")
    if not label:
        return
    ti = d.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path") or ""
    if not path:
        return
    name = os.path.basename(path.rstrip("/")) or path
    failed = "Failure" in (d.get("hook_event_name") or "")
    if failed:
        col, mark = fg(224, 108, 117), DIM + " ✗" + RST   # red verb + ✗ on failure
    else:
        col, mark = COLOR.get(label, DEF), ""
    line = col + label + DIM + "(" + DEF + name + DIM + ")" + RST + mark
    O.emit(sys.argv[1], O.line(line))


if __name__ == "__main__":
    main()
