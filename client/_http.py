# client/_http.py — the daemon's HTTP door, as the clients see it.
#
# Every address, path, header name and environment-variable name the programs in
# this directory use. It is a COPY of the daemon's own vocabulary, deliberately:
# a client that imported the daemon's constants would import the daemon, and the
# one property this directory has is that nothing in it knows the application
# exists. The copy is pinned —
# tests/test_canonical_clients.py::test_the_http_module_matches_the_daemon fails
# the moment any value here drifts from the module that reads it.
#
# Import-pure: one env read and literals.
from __future__ import annotations

from collections.abc import Mapping
import os

HOST = "127.0.0.1"                      # never a routable interface
PORT_VARIABLE = "BAQYLAU_DASHBOARD_PORT"
PORT = int(os.environ.get(PORT_VARIABLE) or 8377)

HOOK_PATH = "/api/harnesses/%s/hooks"
TELEMETRY_PATH = "/api/harnesses/%s/telemetry"
# The pane's three reads, and the whole of what it needs: the aggregate, one
# page of the feed as of the aggregate's cursor, and the stream from that same
# cursor. No width on any of them — the pane wraps its own text now.
SESSION_DATA_PATH = "/sessionData/%s"
SESSION_ENTRIES_PATH = "/sessionData/%s/entries?at=%d"
SESSION_STREAM_PATH = "/sessionData/%s/stream?after_cursor=%d"
PANE_COMMAND_PATHS = {
    "toggle": "/api/terminal/panes/toggle",
    "grow": "/api/terminal/panes/grow",
    "shrink": "/api/terminal/panes/shrink",
    "reset": "/api/terminal/panes/reset",
    "setpct": "/api/terminal/panes/set-percent",
}

# The identity channel: a hook delivery's BODY is the harness's exact stdin, so
# everything the client observed AROUND itself rides beside it in headers.
TERMINAL_WINDOW_HEADER = "X-Baqylau-Terminal-Window"
CLIENT_PROCESS_HEADER = "X-Baqylau-Client-Process"
ACCOUNT_ID_HEADER = "X-Baqylau-Account-Id"
ACCOUNT_NAME_HEADER = "X-Baqylau-Account-Name"
LAUNCH_MODEL_HEADER = "X-Baqylau-Launch-Model"
LAUNCH_EFFORT_HEADER = "X-Baqylau-Launch-Effort"
TELEMETRY_KIND_HEADER = "X-Baqylau-Telemetry-Kind"

# The environment a client reads. Every name here is OWNED elsewhere — the
# launcher sets the launch pair, the account switcher the account pair, the
# terminal the window id — and only OBSERVED here. Forwarded raw: validating a
# slug is the daemon's job (harness/impl/claude_code/account.py).
ACCOUNT_SLUG_VARIABLE = "CLAUDE_SUBSCRIPTION_SLUG"
ACCOUNT_LABEL_VARIABLE = "CLAUDE_SUBSCRIPTION_LABEL"
LAUNCH_MODEL_VARIABLE = "BAQYLAU_LAUNCH_MODEL"
LAUNCH_EFFORT_VARIABLE = "BAQYLAU_LAUNCH_EFFORT"
# The daemon spawns the harness itself to READ something out of it — today, an
# account's plan windows (harness/impl/claude_code/usage/live.py). That process
# runs hooks like any other, and a client that shipped them would put a session
# in the store that nobody started. Seeing this, a client does nothing.
PROBE_VARIABLE = "BAQYLAU_USAGE_PROBE"
WINDOW_ID_VARIABLES = ("KITTY_WINDOW_ID", "BAQYLAU_PTY_WINDOW_ID")


def window_id(environment: Mapping[str, str]) -> str:
    """The terminal window this process runs in, or "".

    The ORIGIN of every window fact in the system: a client runs INSIDE the
    session's own window, so it is the only thing that can observe which one
    that is. Everything downstream receives the answer as a raw event.
    """
    for name in WINDOW_ID_VARIABLES:
        value = (environment.get(name) or "").strip()
        if value:
            return value
    return ""
