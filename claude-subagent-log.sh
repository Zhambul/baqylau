#!/usr/bin/env bash
# claude-subagent-log.sh PHASE — SubagentStart / SubagentStop hook.
#
# Writes the opening/closing frame for a subagent's block in the command mirror.
# PHASE is "start" (SubagentStart) or "stop" (SubagentStop). The inner tool calls
# a subagent makes are mirrored by the SAME PostToolUse hooks as the main agent
# (claude-cmd-log.sh / claude-file-log.sh) — those detect the payload's agent_id
# and render in the subagent's colour — so this wrapper only handles the frame.
# Like the other mirror hooks it just resolves the log path and hands the payload
# to its Python formatter on stdin (the pane width is applied at paint time by
# claude-mirror.py, so it isn't needed here).

phase="${1:-start}"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fmt="$here/claude-subagent-fmt.py"
[ -f "$fmt" ] || exit 0

# The formatter derives the per-session log path from the payload's session_id.
python3 "$fmt" "$phase" 2>/dev/null
exit 0
