# api/config.py — the HTTP server's own policy knobs, and the value object that
# carries them to the routes.
#
# Server-side only: origin admission, the read-only kill switch, the response
# headers every reply carries, caching and compression thresholds, and the boot
# identity. Constants BOTH
# the daemon and its clients read live in core/daemon/contract.py; knobs the dashboard
# presenters own (the static whitelist, notification timing, the public URL)
# stay in dashboard/config.py. Import-pure: env reads + literals only.
#
# The literals below are the SOURCE; `settings()` freezes them into one Settings
# a route receives by injection (api/dependencies.py). Reading a module constant
# on the request path was the last global in this layer: a test could not turn
# the read-only switch on, because it was decided at import.
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Mapping
from urllib.parse import urlsplit

from dashboard.dictate import DEEPGRAM_LISTEN_URL

# The listen(2) backlog. The socketserver-era default of FIVE reset a tunneled
# page refresh's parallel burst of ~16 origin connections; the bound socket keeps the raised value.
REQUEST_QUEUE_SIZE = 128

GZIP_MIN = 1024                    # compress a response body only at/above this size

# Versioned static assets (?v=<BOOT_ID>, api/routes/static.py) are immutable AT
# THAT URL: the stamp changes on every restart, and static bytes only change
# via a restart, so a browser may keep them for the max year. Everything else
# stays no-store.
CACHE_STATIC = "public, max-age=31536000, immutable"

# The origin the BROWSER opens its own socket to. Dictation audio never touches
# this server (it trades an on-disk key for a ~30s JWT and the page speaks wss
# directly), so the one third party in connect-src below is derived from the URL
# dashboard/dictate.py hands out rather than spelled again here.
DICTATION_ORIGIN = "%s://%s" % urlsplit(DEEPGRAM_LISTEN_URL)[:2]

# The Content-Security-Policy every response carries. There is no CORS
# middleware in this tree on purpose: never answering a preflight stops a
# hostile page from READING this origin, and this stops a string that reached
# one of our own pages from ACTING. The two are different halves.
#
# Every directive below was read off the assets it governs, not copied from a
# template — the dashboard is tunneled to real browsers (docs/remote.md) and
# holds everything a session ever said, so a policy that broke the composer would
# just get deleted again.
CONTENT_SECURITY_POLICY = "; ".join((
    "default-src 'self'",
    # index.html is fourteen external <script src> files and NOT one inline
    # script, so 'self' costs nothing. blob: is not a loophole reopening that:
    # it is the dictation AudioWorklet, which must be addressed as a URL and so
    # is compiled from a Blob (app.08-composer.js).
    "script-src 'self' blob:",
    # THE ONE CONCESSION. Server-rendered content is injected with innerHTML and
    # carries inline style attributes — an ANSI span's colour (dashboard/render/
    # ansi.py) and a file verb's (render/items/files.py). style-src-attr would
    # say this more precisely, but Firefox does not implement it and would fall
    # back to a style-src that strips every colour in the feed. script-src above
    # is what actually contains an injection; a style cannot exfiltrate.
    "style-src 'self' 'unsafe-inline'",
    # data: is index.html's inline favicon; blob: is a pasted image's local
    # thumbnail, shown before the bytes have been uploaded anywhere.
    "img-src 'self' data: blob:",
    # THE EXFILTRATION BARRIER, and the reason this header is worth having at
    # all: same origin plus the single third party the page legitimately opens a
    # socket to. Any other address — including one an injected string built —
    # is refused by the browser before a byte of a session leaves the device.
    "connect-src 'self' %s" % DICTATION_ORIGIN,
    "worker-src 'self'",        # /sw.js, the web-push service worker
    "manifest-src 'self'",
    "font-src 'self'",
    "media-src 'none'",         # the dashboard plays nothing
    "object-src 'none'",
    "frame-src 'none'",         # ...and frames nothing
    "base-uri 'none'",          # no injected <base> may re-point a relative URL
    "form-action 'none'",       # every mutation is a guarded fetch, never a form
    "frame-ancestors 'none'",   # nothing may frame the dashboard
))

# Sent with every response, whatever its plane or content type.
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    # The static server answers from a content-type whitelist and the API only
    # ever sends JSON — but a MIME the browser is willing to re-guess is how a
    # stored session body becomes a script, so say it outright.
    "X-Content-Type-Options": "nosniff",
    # A dashboard URL carries a session id, and dictation is a cross-origin
    # request from a page whose URL has one in it. Send the origin, never the path.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # frame-ancestors above is the modern spelling; this is the one older
    # browsers obey, and clickjacking a control plane is the thing it prevents.
    "X-Frame-Options": "DENY",
}

# The only Origins a legit same-origin browser POST carries (it usually sends
# none at all for same-origin fetches; when it does, it is one of these).
# Image content types the composer treats as inline screenshots (thumbnailed,
# and always admitted). Non-image files are still allowed as attachments, just
# size-capped and shown as a filename chip.
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

# This process's identity, sent as the global SSE `ready` event. A page that
# reconnects and sees a DIFFERENT boot id knows the server restarted under it
# and its loaded JS may be stale (the client toasts "refresh").
BOOT_ID = str(int(time.time() * 1000))

# The shared anyio worker pool, raised from its default of 40. Two kinds of work
# borrow from it: a sync route handler for its whole duration, and an SSE stream
# for the length of ONE store read per poll (api/sse.py `off_loop`). A stream
# holds no thread while it waits, so the cap covers concurrent work, not
# concurrent connections — but it is no longer only the request handlers.
THREAD_POOL_TOKENS = 100

# How long a stopping server waits for open connections (the SSE streams never
# close on their own) before force-closing them.
GRACEFUL_SHUTDOWN_SECONDS = 3


@dataclass(frozen=True)
class Settings:
    """This server's policy, as one value. Injected, never imported."""

    session_id_pattern: re.Pattern[str]
    image_mimes: frozenset[str]
    boot_id: str
    cache_static: str
    security_headers: Mapping[str, str]
    gzip_minimum_bytes: int
    thread_pool_tokens: int
    request_queue_size: int
    graceful_shutdown_seconds: int


def settings() -> Settings:
    """The policy this process runs under, read off the constants above."""
    return Settings(
        session_id_pattern=SESSION_ID_PATTERN,
        image_mimes=frozenset(IMAGE_MIMES),
        boot_id=BOOT_ID,
        cache_static=CACHE_STATIC,
        security_headers=SECURITY_HEADERS,
        gzip_minimum_bytes=GZIP_MIN,
        thread_pool_tokens=THREAD_POOL_TOKENS,
        request_queue_size=REQUEST_QUEUE_SIZE,
        graceful_shutdown_seconds=GRACEFUL_SHUTDOWN_SECONDS,
    )
