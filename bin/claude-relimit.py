#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-relimit.py — entry point; the implementation lives in
# plugins/claude_code/relimit.py (docs/relimit.md). This filename is
# load-bearing: it is the audit DB's handler/script vocabulary. Two modes:
#   no argv  — the StopFailure hook handler (payload on stdin; the dispatcher
#              reaches it in-process, this shim is the standalone/test seam)
#   5 argv   — the detached migrator: LOG SID SLUG ALIAS CWD
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
from plugins.claude_code import relimit
if __name__ == "__main__":
    if len(sys.argv) > 1:
        relimit.migrate_entry(sys.argv[1:])
    else:
        relimit.entry()
