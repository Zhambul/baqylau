# core/daemon/contract.py — where the daemon listens, and what it accepts.
#
# The one owner of every constant the SERVER side of the daemon's HTTP door
# reads: the address, the header a control-plane caller stamps, and the
# request-body caps. `core/clients.py` reads the address from here when it builds
# the argv for a client we launch, because a client imports nothing of ours —
# the copy of this vocabulary on the client side of the door is
# `client/_wire.py`, pinned to this file by tests/test_canonical_clients.py.
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

# The composer-attachment upload endpoint carries base64-encoded bytes, so it
# gets its OWN, larger cap — ~14 MiB admits a base64-inflated 10 MB image (the
# per-image ceiling) with headroom for the JSON envelope. Every other POST
# stays at the tiny POST_MAX default.
UPLOAD_MAX = 14 * 1024 * 1024
