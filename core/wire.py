# core/wire.py — the HTTP wire contract shared by the daemon and its clients.
#
# The one owner of every constant BOTH sides of the daemon's HTTP door read:
# where the daemon listens, the header a control-plane caller stamps, the four
# identity headers a hook delivery rides, and the request-body caps. Clients
# (app/daemon_client.py, app/hook_client.py) and the server (api/) import these
# from here so neither side re-encodes the other's vocabulary — and so the
# terminal-side clients depend on no server or presenter package.
# Import-pure: env reads + literals only.
from core import env as EV

HOST_ADDRESS = "127.0.0.1"         # never a routable interface (docs/remote.md)
PORT_NUMBER = EV.env_int("BAQYLAU_DASHBOARD_PORT", 8377)

# Proof of a same-origin control-plane caller (see api/guard.py for the full
# browser-vector defense this header is half of).
POST_HEADER = "X-Baqylau"

# A hook delivery's body is the exact stdin bytes, so what the hook process
# observed around itself rides these flat headers. One fact, two consumers
# each: the thin hook client stamps them, the hook-delivery endpoint reads them.
TERMINAL_WINDOW_HEADER = "X-Baqylau-Terminal-Window"
HARNESS_PROCESS_HEADER = "X-Baqylau-Harness-Process"
ACCOUNT_ID_HEADER = "X-Baqylau-Account-Id"
ACCOUNT_NAME_HEADER = "X-Baqylau-Account-Name"
# Launch-time selections travel in the launched CLI's environment (the
# launcher sets them; the hook process inherits and observes them).
LAUNCH_MODEL_HEADER = "X-Baqylau-Launch-Model"
LAUNCH_EFFORT_HEADER = "X-Baqylau-Launch-Effort"

POST_MAX = 64 * 1024               # request-body cap for the control-plane POSTs
# The composer-attachment upload endpoint carries base64-encoded bytes, so it
# gets its OWN, larger cap — ~14 MiB admits a base64-inflated 10 MB image (the
# per-image ceiling) with headroom for the JSON envelope. Every other POST
# stays at the tiny POST_MAX default.
UPLOAD_MAX = 14 * 1024 * 1024
# A hook delivery carries the harness's exact hook stdin — a post-tool payload
# embeds the whole tool response, so it gets its own generous cap.
HOOK_MAX = 4 * 1024 * 1024
