# dashboard/http/base.py — the HTTP handler's plumbing base + query helpers.
#
# The response/SSE/guard machinery every route shares: gzip-aware _send, the
# JSON + SSE framing, the CORS/preflight/origin control-plane guard, the static
# whitelist server and small request parsers. The concrete
# Handler (http/handler.py) inherits this and mixes GET/POST/SSE in.
import gzip
import json
import os
import re
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from core import audit as A
from dashboard import config
from dashboard.config import (
    BOOT_ID,
    GZIP_MIN,
    POST_HEADER,
    POST_MAX,
    STATIC,
    STATIC_DIR,
    SESSION_ID_PATTERN,
)



class _Base(BaseHTTPRequestHandler):

    def log_message(self, *a):              # stdlib logs to stderr — DEVNULL'd
        pass                                # anyway under spawn_detached

    # -- plumbing --
    def _accepts_gzip(self):
        # honour an explicit `gzip;q=0` refusal; otherwise any gzip token wins.
        for tok in self.headers.get("Accept-Encoding", "").lower().split(","):
            tok = tok.strip()
            if tok == "gzip" or tok.startswith("gzip;"):
                return "q=0" not in tok or "q=0." in tok
        return False

    def _send(self, code, body, ctype="application/json", cache="no-store"):
        # Everything routed through _send is text (JSON/HTML/CSS/JS/plain), so
        # it all compresses; SSE never comes here (it holds the response open
        # and writes incremental frames, which buffering would break). Vary is
        # set whenever the body could vary by encoding, even when this response
        # stays plain, so a shared cache keys the two variants apart.
        # `cache` defaults to no-store — only static() overrides it, for the
        # ?v=<BOOT_ID>-versioned assets (config.CACHE_STATIC).
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Vary", "Accept-Encoding")
        if len(data) >= GZIP_MIN and self._accepts_gzip():
            data = gzip.compress(data)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        try:
            self.wfile.write(data)
        except OSError:
            pass                            # client went away mid-write

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, default=str))

    def _sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def _sse(self, event, obj):
        """One SSE frame; False when the client is gone (ends the loop)."""
        try:
            self.wfile.write(("event: %s\ndata: %s\n\n"
                              % (event, json.dumps(obj, default=str))).encode())
            self.wfile.flush()
            return True
        except OSError:
            return False

    def _sse_beat(self):
        try:
            self.wfile.write(b": beat\n\n")
            self.wfile.flush()
            return True
        except OSError:
            return False

    def _reject(self, code, err):
        """A guard rejection: close the connection (an unread body would desync
        a kept-alive connection) and send the JSON error. Returns None (implicit)
        so the caller can `return self._reject(...)` straight out of _post_guard.

        Audited as a `web-reject` state_files row (path = the rejected request
        path, content = code + reason). This is the ONE place a control-plane
        POST could vanish without a trace: _post_guard rejects BEFORE any
        handler runs, so a browser POST that arrives but fails the guard (a
        missing X-Baqylau header, a cross-origin Origin, read-only mode) wrote
        nothing — the `/stop that produced a client `web-hint op=close` beacon
        yet no `web-stop` row` blind spot. Audit-only telemetry (not an `errors`
        row — an expected 4xx, same rationale as _reject_input), so it never
        lights the errwatch chip."""
        A.state_file("", self.path[:200], "web-reject",
                     {"code": code, "why": err})
        self.close_connection = True
        self._json({"error": err}, code)

    def _post_guard(self, max_bytes=POST_MAX):
        """Validate a control-plane POST against the browser-vector defense
        (see do_POST) and return its parsed JSON body — or send a 4xx and return
        None (the caller just returns). Order: read-only kill switch, content
        type, custom header, Origin, size cap, then the JSON parse.

        `max_bytes` overrides the default POST_MAX cap — the upload endpoint
        raises it to UPLOAD_MAX to admit a base64-inflated image (every other
        caller stays at the tiny control-plane default).

        Two accepted proofs of a same-origin caller, EITHER suffices:
          * the `X-Baqylau` custom header (a cross-origin *simple* POST can't
            set it, and a cross-origin fetch that tries triggers a preflight this
            no-CORS server never answers), OR
          * a present-and-allowlisted `Origin` — because `navigator.sendBeacon`
            CANNOT set a custom header, yet the frontend-audit flush on `pagehide`
            rides sendBeacon (a last-ditch delivery as the tab goes away —
            `flushClog`, docs/dashboard.md *Frontend audit (clientlog)*). (The
            close itself no longer needs this branch: routing it through
            sendBeacon REGRESSED it — queued-then-silently-dropped by the tunnel —
            so it is back on the plain-`fetch` channel that carries the header,
            docs/dashboard.md *Close via the plain-fetch channel*.) A cross-origin
            page cannot forge an allowlisted Origin, and the browser stamps the
            *real* Origin on every cross-origin request, so the Origin allowlist IS
            the CSRF gate here; the header was only ever defence-in-depth. A
            non-allowlisted Origin is still the attack signal and is always
            rejected."""
        raw = self._post_guard_bytes(max_bytes)
        if raw is None:
            return None
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self._reject(400, "invalid JSON")
        if not isinstance(body, dict):
            return self._reject(400, "invalid JSON")
        return body

    def _post_guard_bytes(self, max_bytes=POST_MAX):
        """The same control-plane guard, returning the EXACT body bytes — for
        the one endpoint whose body is evidence (a hook delivery) rather than a
        JSON envelope. Or send a 4xx and return None (the caller just returns)."""
        if config.READONLY:
            return self._reject(403, "control plane disabled (read-only)")
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip()
        if ctype != "application/json":
            return self._reject(415, "content-type must be application/json")
        origin = self.headers.get("Origin")
        if origin and origin not in config.ALLOWED_ORIGINS:
            return self._reject(403, "cross-origin")
        # header OR allowlisted Origin — the pagehide clientlog sendBeacon carries
        # no header but a real allowlisted Origin (a cross-origin caller can forge
        # neither).
        if self.headers.get(POST_HEADER) != "1" and origin not in config.ALLOWED_ORIGINS:
            return self._reject(403, "missing %s header" % POST_HEADER)
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0 or n > max_bytes:
            return self._reject(413, "body too large")
        try:
            return self.rfile.read(n) if n else b""
        except OSError:
            return self._reject(400, "unreadable body")

    def _reject_input(
        self,
        action,
        why,
        message,
        detail,
        code=400,
        log="",
        path="",
    ):
        """Audit and reject malformed application input."""
        A.state_file(log, path, action,
                     dict({"ok": False, "why": why},
                          **{k: repr(v) for k, v in detail.items()}))
        return self._json({"error": message}, code)

    # the SPA parts (app.NN-name.js, split from the former monolithic app.js) are
    # admitted by SHAPE, not a per-file whitelist entry — still no user-path
    # resolution (a strict basename pattern), just no dict bloat for the 13 parts.
    _APP_PART = re.compile(r"^app\.[0-9]{2}-[a-z-]+\.js$")

    def static(self, name):
        ctype = STATIC.get(name)
        if not ctype and self._APP_PART.match(name):
            ctype = "text/javascript; charset=utf-8"
        if not ctype:
            return self._json({"error": "not found"}, 404)
        try:
            with open(os.path.join(STATIC_DIR, name), "rb") as fh:
                data = fh.read()
        except OSError:
            return self._json({"error": "unreadable"}, 500)
        if name == "index.html":
            # CACHE-BUST the sub-resource URLs with BOOT_ID (bumped every
            # restart). The origin sends no-store, but that can't evict an
            # already-cached app.js/style.css in a remote browser (mobile Safari
            # is sticky, and a CDN keys by URL) — so a dashboard update left the
            # phone running old JS forever (the "links don't follow on mobile"
            # report traced here: the fix shipped, the origin served it, the
            # phone kept the pre-fix bytes). index.html itself is served no-store
            # AND is the main document a reload always refetches, so a fresh
            # ?v=<BOOT_ID> reaches the browser and points at a URL nothing has
            # cached. See docs/dashboard.md *Cache-busting*.
            # stamp ?v= on every SPA part (app.NN-*.js) + style.css. The boot
            # audit record reads document.currentScript.src for this same ?v=,
            # so the LAST part (app.13-init.js, where that record now lives) must
            # carry it too — which this covers by stamping them all.
            data = re.sub(rb'(/static/app\.[0-9]{2}-[a-z-]+\.js)',
                          rb'\1?v=' + BOOT_ID.encode(), data)
            data = data.replace(b"/static/style.css", b"/static/style.css?v=" + BOOT_ID.encode())
            # ...and the ICONS, for the same reason: a REGENERATED icon is new
            # bytes at an unchanged URL, so a browser that already has one shows
            # the old glyph indefinitely (mobile Safari keeps a persistent
            # favicon/home-screen icon cache that a hard reload does not evict —
            # the "tab logo doesn't match the page logo" report). The manifest
            # URL is stamped too so a changed icon list is re-read.
            data = re.sub(rb'(/static/(?:apple-touch-icon|icon-[a-z0-9-]+)\.png)',
                          rb'\1?v=' + BOOT_ID.encode(), data)
            data = data.replace(b"/static/manifest.webmanifest",
                                b"/static/manifest.webmanifest?v=" + BOOT_ID.encode())
        if name == "manifest.webmanifest":
            # the manifest's own icon URLs (icon-192/512, maskable) — the
            # installed-app glyph comes from here, not from index.html.
            data = re.sub(rb'(/static/icon-[a-z0-9-]+\.png)',
                          rb'\1?v=' + BOOT_ID.encode(), data)
        # A fetch under the CURRENT boot's ?v=<BOOT_ID> stamp may be cached hard
        # (config.CACHE_STATIC): the URL changes on every restart, and the bytes
        # behind it only change via a restart (the "does NOT hot-reload"
        # contract). This is what keeps a refresh from re-pulling all 14 SPA
        # parts through the tunnel — the burst that overflowed the accept queue
        # and half-loaded the page (docs/dashboard.md *Cache-busting*).
        # index.html and sw.js are fetched un-stamped, so they stay no-store.
        v = (parse_qs(urlparse(self.path).query).get("v") or [""])[0]
        cache = config.CACHE_STATIC if v == BOOT_ID else "no-store"
        return self._send(200, data, ctype, cache=cache)


def valid_session_id(value):
    return bool(SESSION_ID_PATTERN.match(value or ""))


def _qint(url, name):
    try:
        return int((parse_qs(url.query).get(name) or ["0"])[0])
    except ValueError:
        return 0


def _qstr(url, name):
    return (parse_qs(url.query).get(name) or [""])[0]
