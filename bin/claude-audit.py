#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude-audit.py — the audit CLI entry point (formerly root claude_audit.py).
# The implementation lives in core/audit.py (docs/architecture.md); this file
# is the documented CLI:
#
#   python3 bin/claude-audit.py sessions|timeline|errors|anomalies|sql|prune|…
#
# plus the CLI write entry points (`… hook <handler>`, `… error …`).
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root (this file lives in bin/)
from core import audit as _impl

if __name__ == "__main__":
    try:
        _impl.main(sys.argv)
    except BrokenPipeError:
        pass
    except Exception:
        # The CLI write paths are fired from hooks — they must never fail loudly.
        # Derived from core/audit.py's command table so the two can't drift.
        if len(sys.argv) > 1 and sys.argv[1] in _impl.WRITE_COMMANDS:
            pass
        else:
            raise
