#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-codex-hook.py — entry point; the implementation lives in
# plugins/codex/dispatch.py (docs/architecture.md). Wired to codex's native
# NON-SessionStart hook events (~/.codex/hooks.json). This filename is
# load-bearing: it is invoked by name from the codex hooks AND re-invoked by name
# for the detached interrupt-recovery watcher, so argv[0] is the audit DB's
# handler/spawn vocabulary.
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
from plugins.codex import dispatch
if __name__ == "__main__":
    dispatch.entry()
