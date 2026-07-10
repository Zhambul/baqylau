#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-hook.py — entry point; the implementation lives in
# plugins/claude_code/dispatch.py (README § Architecture, § Wiring). This
# filename is load-bearing: ~/.claude/settings.json wires EVERY Claude Code hook
# event to it (one entry per event), and the dispatcher reads hook_event_name to
# fan out in-process to the tab dispatch, formatters, and audit subscriber.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plugins.claude_code import dispatch
if __name__ == "__main__":
    dispatch.entry()
