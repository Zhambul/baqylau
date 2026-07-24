# dashboard/http/base.py — the HTTP handler's plumbing base + query helpers.
#
# The response/SSE/guard machinery every route shares: gzip-aware _send, the
# JSON + SSE framing, the CORS/preflight/origin control-plane guard, the static
# whitelist server, and the _sid/_qint/_qstr request parsers. The concrete
# Handler (http/handler.py) inherits this and mixes GET/POST/SSE in.
import gzip
import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs

from core.noaudit import load_audit
from dashboard import config
from dashboard.config import (BOOT_ID, CLIENTLOG_FIELD_MAX, CLIENTLOG_STR_MAX,
                              GZIP_MIN, POST_HEADER, POST_MAX, STATIC, STATIC_DIR, _SID_OK)

A = load_audit()


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

    def _send(self, code, body, ctype="application/json"):
        # Everything routed through _send is text (JSON/HTML/CSS/JS/plain), so
        # it all compresses; SSE never comes here (it holds the response open
        # and writes incremental frames, which buffering would break). Vary is
        # set whenever the body could vary by encoding, even when this response
        # stays plain, so a shared cache keys the two variants apart.
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Vary", "Accept-Encoding")
        if len(data) >= GZIP_MIN and self._accepts_gzip():
            data = gzip.compress(data)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
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
        missing X-Claude-Dash header, a cross-origin Origin, read-only mode) wrote
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
          * the `X-Claude-Dash` custom header (a cross-origin *simple* POST can't
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
            raw = self.rfile.read(n) if n else b""
            body = json.loads(raw or b"{}")
        except (ValueError, OSError):
            return self._reject(400, "invalid JSON")
        if not isinstance(body, dict):
            return self._reject(400, "invalid JSON")
        return body

    @staticmethod
    def _clip_scalars(d):
        """Keep only JSON scalars from a client-supplied dict — bounded count,
        strings capped — so telemetry can't smuggle bulk/nesting into the audit."""
        out = {}
        for k, v in list(d.items())[:CLIENTLOG_FIELD_MAX]:
            if not isinstance(k, str):
                continue
            if isinstance(v, bool) or isinstance(v, (int, float)):
                out[k] = v
            elif isinstance(v, str):
                out[k] = v[:CLIENTLOG_STR_MAX]
        return out

    def _reject_input(self, action, why, message, detail, code=400,
                      log="", path=""):
        """A control-plane INPUT-validation reject (the client sent a bad
        field). Audited as an `ok:False` state_files row under the handler's own
        `action` vocabulary, carrying the reason (`why`) and the EXACT received
        bytes (repr — a remote client's "but I picked it from the dropdown" is
        undebuggable otherwise, invisible characters included). Deliberately NOT
        an `errors` row: these are expected 4xx from client input (an
        abandoned/partial cwd, a typo'd model, a bad quick-command), not
        swallowed exceptions — their traceback would be a bare `NoneType: None`
        — and must not light the errwatch warning chip, which surfaces every
        session_id='' `errors` row as a `⚠ global:` in EVERY session's
        scorebar. This is the shape `post_dictate_token` already used inline for
        its bad-rate reject; the reject sites that mis-used A.error now share it.
        Distinct from `_reject` (the low-level guard rejection that closes the
        connection because it hasn't read the body — the input body is already
        consumed by `_post_guard` here, so no desync to guard against).

        `log`/`path` file the row under a SESSION (the session-scoped POSTs —
        web-send/web-rename/web-answer/…, so a rejected attempt lands in THAT
        session's audit timeline, not just the global stream); default '' keeps
        the GLOBAL row the session-less endpoints (web-launch/notify-mute/
        hide-dir/dictate) already relied on. Without this every empty-message /
        empty-name / bad-payload reject was a silent 4xx — the same class of
        blind spot `_reject` closed for guard rejections. Returns the response
        so callers stay `return self._reject_input(...)`."""
        A.state_file(log, path, action,
                     dict({"ok": False, "why": why},
                          **{k: repr(v) for k, v in detail.items()}))
        return self._json({"error": message}, code)

    def static(self, name):
        ctype = STATIC.get(name)
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
            data = data.replace(b"/static/app.js", b"/static/app.js?v=" + BOOT_ID.encode())
            data = data.replace(b"/static/style.css", b"/static/style.css?v=" + BOOT_ID.encode())
        return self._send(200, data, ctype)


def _sid(s):
    return bool(_SID_OK.match(s or ""))


def _qint(url, name):
    try:
        return int((parse_qs(url.query).get(name) or ["0"])[0])
    except ValueError:
        return 0


def _qstr(url, name):
    return (parse_qs(url.query).get(name) or [""])[0]
