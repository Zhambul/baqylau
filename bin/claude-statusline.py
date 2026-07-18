#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-statusline.py — the status-line shim ENTRY (docs/dashboard.md,
# "Accounts & usage"). Wired NOT as a hook but as settings.json's
# `statusLine.command`: it reads the status-line stdin, stashes this session's
# rate limits + account into the state DB, then runs the REAL status-line
# command passed as argv[1:] (the user's HUD) with that same stdin, forwarding
# its output. Implementation in plugins/claude_code/statusline.py; this
# filename is the audit script vocabulary. The shim must never break the
# status line — every capture failure is swallowed and the delegate still runs.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
from plugins.claude_code import statusline
if __name__ == "__main__":
    statusline.main()
