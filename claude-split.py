#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-split.py — entry point; the implementation lives in
# plugins/claude_code/split.py (docs/architecture.md). This filename is
# load-bearing: the hook wiring in ~/.claude/settings.json invokes it, and
# argv[0] is the audit DB's handler/script vocabulary.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plugins.claude_code import split
if __name__ == "__main__":
    split.entry()
