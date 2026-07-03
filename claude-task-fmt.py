#!/usr/bin/env python3
# claude-task-fmt.py MIRROR_LOG WIDTH
#
# Renders an agent-team shared-task-list event into the command mirror as one
# compact line. Driven by the TaskCreated / TaskCompleted hooks (which fire in the
# lead session when the team creates / completes a task). The event name is read
# from the hook payload (hook_event_name), so no phase argument is needed.
#
#   TaskCreated   -> "✚ task #<id> · <subject>"   (amber)
#   TaskCompleted -> "✓ task #<id> · <subject>"   (green)
#
# Empirically the payload carries task_id + task_subject + task_description (NOT the
# "task_title"/"task_status" the docs mention). There is no readable per-task file
# on disk, so the hook payload is the source of truth.
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import claude_ops as O

A = O.A    # audit trail (real module, or a no-op stub if it failed to import)

CREATED_RGB = (214, 153, 92)    # warm amber — a task entering the list
DONE_RGB    = (152, 195, 121)   # green — a task finished


def main():
    try:
        d = json.load(sys.stdin)
    except Exception:
        return
    LOG = O.log_path(d)
    ev   = d.get("hook_event_name") or ""
    tid  = d.get("task_id") or "?"
    subj = d.get("task_subject") or d.get("task_title") or d.get("task_description") or ""
    if ev == "TaskCompleted":
        glyph, rgb = "✓", DONE_RGB
    else:                                    # TaskCreated (or anything task-ish)
        glyph, rgb = "✚", CREATED_RGB
    text = f"{glyph} task #{tid} · {subj}" if subj else f"{glyph} task #{tid}"
    O.emit(LOG, O.blank(), O.label(text, rgb))
    A.hook_event(d, decision=f"rendered: {text}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        A.error("", "main")
