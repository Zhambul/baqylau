"""The environment the child processes get — the developer's own, not the suite's.

`tests/conftest.py` isolates every test from the real data directory and the
real Claude config. That is right for the hermetic suite and WRONG here: a real
harness needs its real config directory to find its credentials and the hooks
the developer actually installed, and upstream drift is exactly what that
configuration produces. Pointing `CLAUDE_CONFIG_DIR` at a tmpdir would test a
harness that has never been logged in.

So the environment is snapshotted at IMPORT time — collection, before any
fixture has monkeypatched anything — and every child process is launched from
that copy with only the variables this suite owns overwritten. The variables
this suite owns are exactly two: where the databases live, and which port the
hooks report to.
"""

from __future__ import annotations

import os

PRISTINE_ENVIRONMENT: dict[str, str] = dict(os.environ)

# Variables that identify a LIVE session, agent or window belonging to whoever
# started the suite — as opposed to variables that say where configuration lives,
# which are kept. Inheriting these makes a launched harness believe it is a child
# of the one running the tests, and the consequences are not subtle:
#
#   CLAUDE_CODE_CHILD_SESSION   Claude Code turns TRANSCRIPT SAVING OFF. Measured:
#                               the first run of this suite from inside a Claude
#                               Code session produced hook evidence and not one
#                               transcript record, because there was no file.
#   CLAUDE_EFFORT               read at the TOP of Claude's effort precedence, so
#                               it silently overrides the `--effort` under test.
#   CLAUDE_OTEL_PORT            would post this session's usage telemetry to the
#                               developer's own daemon instead of the test's.
#   BAQYLAU_LAUNCH_MODEL/EFFORT the launcher sets these itself; an inherited pair
#                               would report the parent's selection as this one's.
#   KITTY_WINDOW_ID             a pseudo-terminal is not a kitty window, and the
#                               hooks forward this as the session's terminal.
#
# This is why the suite runs the harness from a scrubbed copy rather than from
# `os.environ`: a leak here does not fail loudly, it produces a session that is
# subtly not the one the scenario asked for.
SESSION_IDENTITY_VARIABLES = (
    "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "CLAUDE_OTEL_PORT",
    "CODEX_COMPANION_SESSION_ID",
    "BAQYLAU_LAUNCH_MODEL",
    "BAQYLAU_LAUNCH_EFFORT",
    "KITTY_WINDOW_ID",
)


def child_environment(**overrides: str | int | None) -> dict[str, str]:
    """The pristine environment, scrubbed of session identity, plus this suite's
    overrides (None drops a variable)."""
    environment = dict(PRISTINE_ENVIRONMENT)
    for name in SESSION_IDENTITY_VARIABLES:
        environment.pop(name, None)
    for name, value in overrides.items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = str(value)
    return environment
