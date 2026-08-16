# api/config.py — the HTTP server's own policy knobs.
#
# Server-side only: origin admission, the read-only kill switch, response
# caching and compression thresholds, and the boot identity. Constants BOTH
# the daemon and its clients read live in core/daemon/contract.py; knobs the dashboard
# presenters own (the static whitelist, notification timing, the public URL)
# stay in dashboard/config.py. Import-pure: env reads + literals only.
import os
import re
import time

from core.daemon.contract import HOST_ADDRESS, PORT_NUMBER
from dashboard.config import PUBLIC_URL

LOCK_KEY = "dashboard"

# The listen(2) backlog. The socketserver-era default of FIVE reset a tunneled
# page refresh's parallel burst of ~16 origin connections (docs/dashboard.md
# *Cache-busting*); the bound socket keeps the raised value.
REQUEST_QUEUE_SIZE = 128

GZIP_MIN = 1024                    # compress a response body only at/above this size

# Versioned static assets (?v=<BOOT_ID>, api/routes/static.py) are immutable AT
# THAT URL: the stamp changes on every restart, and static bytes only change
# via a restart, so a browser may keep them for the max year. Everything else
# stays no-store.
CACHE_STATIC = "public, max-age=31536000, immutable"

# The only Origins a legit same-origin browser POST carries (it usually sends
# none at all for same-origin fetches; when it does, it is one of these).
# BAQYLAU_DASHBOARD_ORIGINS extends the set for a proxied deployment
# (docs/remote.md): comma-separated FULL origins, scheme and all. The knob adds
# origins, never replaces the local ones, and is NOT an exposure switch — the
# bind stays 127.0.0.1; only an outbound connector on this machine can front
# the port.
def extra_origins(raw):
    """BAQYLAU_DASHBOARD_ORIGINS → the set of extra allowed origins
    (comma-separated, whitespace-tolerant, empty entries dropped)."""
    return {origin.strip() for origin in (raw or "").split(",") if origin.strip()}


ALLOWED_ORIGINS = ({"http://%s:%d" % (HOST_ADDRESS, PORT_NUMBER),
                    "http://localhost:%d" % PORT_NUMBER,
                    PUBLIC_URL}
                   | extra_origins(os.environ.get("BAQYLAU_DASHBOARD_ORIGINS")))

# BAQYLAU_DASHBOARD_READONLY=1 switches the control plane off entirely (every
# POST is 403) — remote eyes, no remote hands, whatever the proxy in front allows.
READONLY = (os.environ.get("BAQYLAU_DASHBOARD_READONLY") or "") == "1"

# Image content types the composer treats as inline screenshots (thumbnailed,
# and always admitted). Non-image files are still allowed as attachments, just
# size-capped and shown as a filename chip.
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

# This process's identity, sent as the global SSE `ready` event. A page that
# reconnects and sees a DIFFERENT boot id knows the server restarted under it
# and its loaded JS may be stale (the client toasts "refresh").
BOOT_ID = str(int(time.time() * 1000))

# Sync route handlers run on the shared worker-thread pool; SSE streams are
# async and cost no thread, so this only has to cover the short-lived request
# handlers. Raised from the anyio default of 40 as burst headroom.
THREAD_POOL_TOKENS = 100

# How long a stopping server waits for open connections (the SSE streams never
# close on their own) before force-closing them.
GRACEFUL_SHUTDOWN_SECONDS = 3
