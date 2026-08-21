# harness/hooks/headers.py — the hook delivery's own identity vocabulary.
#
# A hook delivery's BODY is the exact stdin bytes the harness wrote, so
# everything the hook process observed around itself has to ride beside them.
# These headers are that channel, and they have exactly two readers each: a hook
# client in `client/` stamps them, the hook-delivery endpoint reads them
# (api/hooks/routes.py). The client's copy of these names lives in
# client/_wire.py and is pinned to this file by tests/test_canonical_clients.py —
# a client may not import the application.
#
# They live here rather than in core/daemon/contract.py because they are HARNESS
# vocabulary — an account, a CLI process, a launch selection — not general
# daemon plumbing. The generic half (host, port, the control-plane guard
# header, the ordinary body caps) stays in core/daemon/contract.py.
# Import-pure: literals only.

TERMINAL_WINDOW_HEADER = "X-Baqylau-Terminal-Window"
# The client's OWN pid, not the CLI's. A client observes; the daemon interprets
# — and the ancestry walk it takes to name the CLI needs the harness's process
# name, which is plugin vocabulary the client must not import. The chain is
# provably alive while the daemon walks it: the CLI is blocked on this very
# delivery's response.
CLIENT_PROCESS_HEADER = "X-Baqylau-Client-Process"
ACCOUNT_ID_HEADER = "X-Baqylau-Account-Id"
ACCOUNT_NAME_HEADER = "X-Baqylau-Account-Name"
# Launch-time selections travel in the launched CLI's environment (the
# launcher sets them; the hook process inherits and forwards them raw).
LAUNCH_MODEL_HEADER = "X-Baqylau-Launch-Model"
LAUNCH_EFFORT_HEADER = "X-Baqylau-Launch-Effort"

# A hook delivery carries the harness's exact hook stdin — a post-tool payload
# embeds the whole tool response, so it gets its own generous cap, far above
# the tiny `core.daemon.contract.POST_MAX` every other POST stays at.
HOOK_MAX = 4 * 1024 * 1024
