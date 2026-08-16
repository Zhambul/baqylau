# harness/hooks/wire.py — the hook delivery's own wire vocabulary.
#
# A hook delivery's BODY is the exact stdin bytes the harness wrote, so
# everything the hook process observed around itself has to ride beside them.
# These headers are that channel, and they have exactly two readers each: the
# thin hook client stamps them (harness/hooks/client.py), the hook-delivery
# endpoint reads them (api/common/hooks.py).
#
# They live here rather than in core/wire.py because they are HARNESS
# vocabulary — an account, a CLI process, a launch selection — not general
# daemon plumbing. The generic half (host, port, the control-plane guard
# header, the ordinary body caps) stays in core/wire.py.
# Import-pure: literals only.

TERMINAL_WINDOW_HEADER = "X-Baqylau-Terminal-Window"
HARNESS_PROCESS_HEADER = "X-Baqylau-Harness-Process"
ACCOUNT_ID_HEADER = "X-Baqylau-Account-Id"
ACCOUNT_NAME_HEADER = "X-Baqylau-Account-Name"
# Launch-time selections travel in the launched CLI's environment (the
# launcher sets them; the hook process inherits and observes them).
LAUNCH_MODEL_HEADER = "X-Baqylau-Launch-Model"
LAUNCH_EFFORT_HEADER = "X-Baqylau-Launch-Effort"

# A hook delivery carries the harness's exact hook stdin — a post-tool payload
# embeds the whole tool response, so it gets its own generous cap, far above
# the tiny `core.daemon.wire.POST_MAX` every other POST stays at.
HOOK_MAX = 4 * 1024 * 1024
