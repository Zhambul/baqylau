# Copyright (c) 2026 Zhambyl Yermagambet
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

import os
from types import MappingProxyType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

HOST = "127.0.0.1"  # never a routable interface
PORT_VARIABLE = "BAQYLAU_DASHBOARD_PORT"
DEFAULT_PORT = 8377
PORT = int(os.environ.get(PORT_VARIABLE) or DEFAULT_PORT)

HOOK_PATH = "/api/harnesses/%s/hooks"
TELEMETRY_PATH = "/api/harnesses/%s/telemetry"
# The pane's three reads, and the whole of what it needs: the aggregate, one
# page of the feed as of the aggregate's cursor, and the stream from that same
# cursor. No width on any of them — the pane wraps its own text now.
SESSION_DATA_PATH = "/sessionData/%s"
SESSION_ENTRIES_PATH = "/sessionData/%s/entries?at=%d"
SESSION_STREAM_PATH = "/sessionData/%s/stream?after_cursor=%d&after_entry=%d"
# An extension pane reads its view at the pane size; the host presents it.
EXTENSION_VIEW_PATH = "/api/extension-terminal/views/%s/%s?scope=%s&columns=%d&rows=%d"
# The pane repaints after each committed record change of its extension and scope.
EXTENSION_CHANGES_PATH = "/api/extensions/%s/changes?scope=%s&projection_generation=%s&cursor=%d"
# The pane selector lists the views of a window, then opens one beside it.
EXTENSION_VIEWS_PATH = "/api/extension-terminal/views?window_id=%s"
EXTENSION_PANES_PATH = "/api/extension-terminal/panes"
# The pane asks the daemon to run the action of its focused item.
EXTENSION_ACTIONS_PATH = "/api/extension-terminal/actions"
# A session mirror or scoreboard reads the extension sections that name it.
EXTENSION_SECTIONS_PATH = "/api/extension-terminal/sessions/%s/%s?columns=%d&rows=%d"
PANE_COMMAND_PATHS = MappingProxyType({
    "toggle": "/api/terminal/panes/toggle",
    "grow": "/api/terminal/panes/grow",
    "shrink": "/api/terminal/panes/shrink",
    "reset": "/api/terminal/panes/reset",
    "setpct": "/api/terminal/panes/set-percent",
})

# The identity channel: a hook delivery's BODY is the harness's exact stdin, so
# everything the client observed AROUND itself rides beside it in headers.
TERMINAL_WINDOW_HEADER = "X-Baqylau-Terminal-Window"
CLIENT_PROCESS_HEADER = "X-Baqylau-Client-Process"
LAUNCH_MODEL_HEADER = "X-Baqylau-Launch-Model"
LAUNCH_EFFORT_HEADER = "X-Baqylau-Launch-Effort"
TELEMETRY_KIND_HEADER = "X-Baqylau-Telemetry-Kind"

# The environment a client reads. Every name here is OWNED elsewhere — the
# launcher sets the launch pair and the terminal sets the window id. The client
# only observes these values.
LAUNCH_MODEL_VARIABLE = "BAQYLAU_LAUNCH_MODEL"
LAUNCH_EFFORT_VARIABLE = "BAQYLAU_LAUNCH_EFFORT"
# The daemon spawns the harness itself to READ something out of it — today, an
# plan windows (harness/impl/claude_code/usage/live.py). That process
# runs hooks like any other, and a client that shipped them would put a session
# in the store that nobody started. Seeing this, a client does nothing.
PROBE_VARIABLE = "BAQYLAU_USAGE_PROBE"
INTERNAL_MODEL_VARIABLE = "BAQYLAU_INTERNAL_MODEL"
WINDOW_ID_VARIABLES = ("KITTY_WINDOW_ID", "BAQYLAU_PTY_WINDOW_ID")


def window_id(environment: Mapping[str, str]) -> str:
    """Return the window ID.

    The terminal window this process runs in, or "".

        The ORIGIN of every window fact in the system: a client runs INSIDE the
        session's own window, so it is the only thing that can observe which one
        that is. Everything downstream receives the answer as a raw event.

    Returns:
        Window ID.

    """
    for name in WINDOW_ID_VARIABLES:
        window_id = (environment.get(name) or "").strip()
        if window_id:
            return window_id
    return ""
