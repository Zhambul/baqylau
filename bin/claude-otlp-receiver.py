#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-otlp-receiver.py — entry point; the implementation lives in
# plugins/otel/receiver.py (docs/architecture.md). This filename is load-bearing:
# it is spawned by filename and argv[0] is the audit DB's stream/handler
# vocabulary.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
from plugins.otel import receiver
if __name__ == "__main__":
    receiver.entry()
