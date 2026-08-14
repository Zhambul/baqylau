"""Remaining application file routes awaiting named service extraction."""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from core import audit as AUDIT
from dashboard.http.post.files import _FilesMixin



class _PostMixin(_FilesMixin):
    """Route only non-semantic file and dictation application requests."""

    _ROUTES = {
        ("application", "uploads"): _FilesMixin.post_upload,
        ("application", "clipboard-files"): _FilesMixin.post_clipboard_files,
        ("application", "dictation-token"): _FilesMixin.post_dictate_token,
    }

    def do_POST(self):
        parts = [
            unquote(part)
            for part in urlparse(self.path).path.strip("/").split("/")
            if part
        ]
        try:
            self.route_post(parts)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            AUDIT.error("", "dashboard POST", {"path": self.path[:200]})
            try:
                self._json({"error": "internal"}, 500)
            except Exception:
                pass

    def route_post(self, parts):
        api = parts[1:] if parts[:1] == ["api"] else None
        handler = self._ROUTES.get(tuple(api or ()))
        if handler is None:
            return self._json({"error": "not found"}, 404)
        return handler(self)
