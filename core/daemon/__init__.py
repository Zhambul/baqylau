"""The daemon's door, both sides of it.

    wire.py    where the daemon listens, the header a caller stamps, the caps
    client.py  the thin HTTP client every process outside the daemon speaks

One owner for the constants, so the server (`api/`) and its clients (the pane
renderers, the keybinding and click handlers, the hook processes) never
re-encode each other's vocabulary — and so a client depends on no server or
presenter package, and imports no web framework.
"""
