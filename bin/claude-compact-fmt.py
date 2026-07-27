#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-compact-fmt.py — entry point; the implementation lives in
# plugins/claude_code/compact_fmt.py (docs/architecture.md). This filename is
# load-bearing: it is the audit DB's handler/script vocabulary for the
# compaction-in-progress latch behind the web dashboard's animated ctx bar.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
from plugins.claude_code import compact_fmt
if __name__ == "__main__":
    compact_fmt.entry()
