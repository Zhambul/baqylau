# core/daemon/contract.py — the agreement between the daemon and its clients.
#
# The one owner of every constant BOTH sides of the daemon's HTTP door read:
# where the daemon listens, the header a control-plane caller stamps, and the
# request-body caps. Clients (core/daemon/client.py) and the server (api/)
# import these from here so neither side re-encodes the other's vocabulary —
# and so the terminal-side clients depend on no server or presenter package.
#
# The HOOK delivery's own vocabulary — the identity headers a hook process
# stamps and its body cap — is harness vocabulary and lives with the channel it
# belongs to, in harness/hooks/headers.py.
# Import-pure: env reads + literals only.
from core import env as EV

HOST_ADDRESS = "127.0.0.1"         # never a routable interface (docs/remote.md)
PORT_NUMBER = EV.env_int("BAQYLAU_DASHBOARD_PORT", 8377)

# Proof of a same-origin control-plane caller (see api/guard.py for the full
# browser-vector defense this header is half of).
POST_HEADER = "X-Baqylau"

POST_MAX = 64 * 1024               # request-body cap for the control-plane POSTs
# The composer-attachment upload endpoint carries base64-encoded bytes, so it
# gets its OWN, larger cap — ~14 MiB admits a base64-inflated 10 MB image (the
# per-image ceiling) with headroom for the JSON envelope. Every other POST
# stays at the tiny POST_MAX default.
UPLOAD_MAX = 14 * 1024 * 1024
