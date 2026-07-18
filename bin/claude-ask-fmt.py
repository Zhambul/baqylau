#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-ask-fmt.py — entry point; the implementation lives in
# plugins/claude_code/ask_fmt.py (docs/architecture.md). This filename is
# load-bearing: it is the audit DB's handler/script vocabulary for the
# AskUserQuestion pending-state tracker behind the web dashboard's ask card.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
from plugins.claude_code import ask_fmt
if __name__ == "__main__":
    ask_fmt.entry()
