#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-mail-fmt.py — entry point; the implementation lives in
# plugins/claude_code/mail_fmt.py (docs/architecture.md). This filename is
# load-bearing: the dispatcher runs it under this identity and argv[0] is the
# audit DB's handler/script vocabulary.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
from plugins.claude_code import mail_fmt
if __name__ == "__main__":
    mail_fmt.entry()
