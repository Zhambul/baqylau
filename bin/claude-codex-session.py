#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-codex-session.py — entry point; the implementation lives in
# plugins/codex/session.py (docs/architecture.md). Wired to codex's native
# SessionStart hook (~/.codex/hooks.json). This filename is load-bearing: it is
# invoked by name from the codex hook and argv[0] is the audit DB's handler
# vocabulary.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
from plugins.codex import session
if __name__ == "__main__":
    session.entry()
