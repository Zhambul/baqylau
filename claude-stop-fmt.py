#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-stop-fmt.py — entry point; the implementation lives in
# plugins/claude_code/stop_fmt.py (docs/architecture.md). This filename is
# load-bearing: the hook wiring in ~/.claude/settings.json invokes it, and
# argv[0] is the audit DB's handler/script vocabulary.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plugins.claude_code import stop_fmt
if __name__ == "__main__":
    stop_fmt.entry()
