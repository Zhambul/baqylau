#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-codex-watch.py — entry point; the implementation lives in
# plugins/codex/watch.py (README § Architecture). This filename is load-bearing:
# it is spawned by filename and argv[0] is the audit DB's spawn/handler
# vocabulary.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plugins.codex import watch
if __name__ == "__main__":
    watch.entry()
