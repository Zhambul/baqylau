# core/env.py — the ONE reader of a NUMERIC environment knob.
#
# Every tuning knob in this repo follows the `CLAUDE_*` convention and is read
# once, at module import, into a named constant (docs/styleguide.md, *Magic
# values*). The reads themselves used to be spelled inline, ~20 times, as
#
#     POLL_S = float(os.environ.get("CLAUDE_TAIL_POLL_S") or 0.4)
#
# which is correct until the value isn't a number. Then `float()` raises
# ValueError — at IMPORT time, inside whatever process imported the module. For
# core/tail.py and plugins/claude_code/stream.py that process is a HOOK, and a
# raise there is the one failure the "hooks must never block or fail" invariant
# cannot cover: there is no handler yet, no `A.error` call has run, and the
# process exits non-zero with a traceback on stderr. A typo in a shell profile
# would take out every hook on the machine, silently and globally.
#
# Two modules had already noticed and each grown their own guard
# (`dashboard.config._float_env`, `plugins.claude_code.model.int_env`); the
# other ~20 sites had none. So the guard gets one owner, here, in the most-core
# module whose charter fits — a stdlib-only leaf like core/paths.py, importable
# from every tier.
#
# The contract is deliberately forgiving in one direction only: a missing,
# empty, blank, or unparseable value falls back to `default`, and so does a
# NEGATIVE one (no knob in this repo — a poll interval, a byte cap, a grace
# window, a port — has a meaningful negative value, and a negative poll is an
# infinite spin). Zero IS allowed: `CLAUDE_WATCH_POLL_S=0` is the test suite's
# documented "leave every sleep its literal" sentinel (docs/testing.md).
import os


def _read(name, default, cast):
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return cast(default)
    try:
        v = cast(raw)
    except (TypeError, ValueError):
        return cast(default)
    return v if v >= 0 else cast(default)


def env_float(name, default):
    """Float env knob `name`, or `default` when it is missing / empty /
    unparseable / negative. Never raises — see the module header for why that
    matters at import time in a hook."""
    return _read(name, default, float)


def env_int(name, default):
    """Int env knob `name`, or `default` when it is missing / empty /
    unparseable / negative. The integer twin of env_float; a value with a
    fractional part is unparseable and falls back (a byte cap or a port is not
    a float that got rounded — it is a typo)."""
    return _read(name, default, int)
