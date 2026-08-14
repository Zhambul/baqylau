"""Static assets and the two fixed application feature probes."""
from urllib.parse import unquote, urlparse

from core import audit as A
from dashboard import dictate, webpush



class _GetMixin:

    # -- routing --
    def do_GET(self):
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/") if p]
        try:
            self.route(url, parts)
        except (BrokenPipeError, ConnectionResetError):
            pass                            # client disconnect is not an error
        except Exception:
            A.error("", "dashboard request", {"path": self.path[:200]})
            try:
                self._json({"error": "internal"}, 500)
            except Exception:
                pass

    # Each fixed resource maps directly to its handler.
    _FIXED_GET = {
        ("application", "dictation"): "get_dictate",
        ("application", "push-configuration"): "get_push_config",
    }
    def route(self, url, parts):
        if not parts:
            return self.static("index.html")
        if parts[0] == "static" and len(parts) == 2:
            return self.static(parts[1])
        if parts == ["sw.js"]:
            # the push service worker, served at the root so its scope is the
            # whole origin (docs/dashboard.md *Web push*) — not under /static/,
            # which would scope it to /static/.
            return self.static("sw.js")
        if parts == ["favicon.ico"]:
            # the raster fallback favicon, at the root path clients probe on
            # their own when the declared SVG icon is unusable (iOS Safari has
            # no SVG-favicon support at all). Undeclared on purpose — see
            # config.STATIC. docs/dashboard.md *Favicon fallback*.
            return self.static("favicon.ico")
        if parts[0] != "api":
            return self._not_found()
        api = parts[1:]
        fixed = self._FIXED_GET.get(tuple(api))
        if fixed:
            return getattr(self, fixed)(url)
        return self._not_found()

    def _not_found(self):
        return self._json({"error": "not found"}, 404)

    # -- the fixed read endpoints --
    def get_dictate(self, url):
        """feature probe: the page renders mic buttons iff a Deepgram key is
        configured (docs/dashboard.md *Web dictation*) — no key means the feature
        is invisible, never a dead button."""
        return self._json({"available": dictate.available()})

    def get_push_config(self, url):
        """the Web Push feature probe (docs/dashboard.md *Web push*): the page
        offers the notification opt-in + subscribes only when push is possible
        AND has an application-server key. `enabled` false (no crypto backend /
        no key) keeps the feature invisible, never a dead button. The public key
        is not a secret."""
        key = webpush.public_key()
        return self._json({"enabled": bool(webpush.enabled() and key),
                           "key": key})
