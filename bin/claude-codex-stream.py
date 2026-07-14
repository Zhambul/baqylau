#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-codex-stream.py — entry point; the implementation lives in
# plugins/codex/stream.py (docs/architecture.md). This filename is load-bearing:
# it is spawned by filename and argv[0] is the audit DB's spawn/handler
# vocabulary.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
from plugins.codex import stream
if __name__ == "__main__":
    stream.entry()
