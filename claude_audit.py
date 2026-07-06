#!/Users/z.yermagambet/.pyenv/versions/3.12.1/bin/python3
# claude_audit.py — compat shim + the audit CLI entry point. The implementation
# moved to core/audit.py (README § Architecture); `import claude_audit` yields
# that same module object, and this file remains the documented CLI:
#
#   python3 claude_audit.py sessions|timeline|errors|anomalies|sql|prune|…
#
# plus the write entry points hooks invoke (`claude_audit.py hook subscriber`).
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import audit as _impl

if __name__ == "__main__":
    try:
        _impl.main(sys.argv)
    except BrokenPipeError:
        pass
    except Exception:
        # The CLI write paths are fired from hooks — they must never fail loudly.
        if len(sys.argv) > 1 and sys.argv[1] in ("session-start", "session-end",
                                                 "hook", "transition", "error",
                                                 "pane", "state-file"):
            pass
        else:
            raise
else:
    sys.modules[__name__] = _impl
