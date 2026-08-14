# dashboard/http/post/files.py — bytes and paths: staging a composer ATTACHMENT
# on disk and handing back the `@path` mention that delivers it, resolving the
# full paths of files pasted as zero-byte promises off the host pasteboard, and
# minting the short-lived Deepgram grant the browser dictates through.
import base64
import re
import binascii
import os
import uuid

from dashboard import paths
from core import audit as A
from dashboard import (clipboard, dictate)
from dashboard import config
from dashboard.config import (IMAGE_MIMES)
from dashboard.http.base import valid_session_id
from contracts.harness import QueryContext



class _FilesMixin:
    """Attachment staging, clipboard path resolution, dictation grants."""

    def post_upload(self):
        """Stage a composer ATTACHMENT (an image/screenshot the browser pasted,
        dropped, or picked, or any other file) on disk, and hand back the
        ABSOLUTE path the composer will inject as an `@path` mention (post_message
        / post_new_session). Body: {session_id?, name, mime, data(base64)}.

        Transport is JSON+base64, NOT multipart: it keeps the whole _post_guard
        browser-vector defense (same-origin + custom header + read-only switch)
        with no boundary parser to write; the price is a base64 envelope, which
        UPLOAD_MAX budgets for. The bytes land under the application data
        directory, outside any repository working tree,
        in a per-session subdir; this mkdir is a sanctioned control-plane write
        (gated by _post_guard/READONLY like every other mutating POST).

        docs/dashboard.md, *Web attachments*."""
        body = self._post_guard(config.UPLOAD_MAX)
        if body is None:
            return
        session_id = body.get("session_id")
        session_id = (
            session_id
            if isinstance(session_id, str) and valid_session_id(session_id)
            else ""
        )
        log, state_path = "", ""
        name = body.get("name")
        mime = body.get("mime") or ""
        encoded_data = body.get("data")
        if not isinstance(name, str) or not isinstance(encoded_data, str) \
                or not isinstance(mime, str):
            return self._reject_input(
                "web-upload", "bad fields", "name, mime, data required",
                {"name": name, "mime": mime}, log=log, path=state_path)
        # basename only — strip any path component a hostile name carries, and
        # fall back to a neutral stem so an empty/dotfile name can't produce a
        # bare-uuid or hidden file.
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name)).lstrip(".")
        safe_name = safe_name[:80] or "attachment"
        try:
            file_bytes = base64.b64decode(encoded_data, validate=True)
        except (binascii.Error, ValueError):
            return self._reject_input("web-upload", "bad base64",
                                      "invalid base64", {"name": safe_name},
                                      log=log, path=state_path)
        if not file_bytes:
            return self._reject_input("web-upload", "empty file", "empty file",
                                      {"name": safe_name}, log=log, path=state_path)
        if len(file_bytes) > config.UPLOAD_MAX:
            return self._reject_input("web-upload", "too large",
                                      "file too large", {"bytes": len(file_bytes)},
                                      code=413, log=log, path=state_path)
        is_image = mime in IMAGE_MIMES
        destination_directory = paths.session_uploads_directory(session_id)
        path = os.path.join(
            destination_directory,
            "%s-%s" % (uuid.uuid4().hex[:8], safe_name),
        )
        try:
            os.makedirs(destination_directory, exist_ok=True)
            with open(path, "wb") as destination_file:
                destination_file.write(file_bytes)
        except OSError as error:
            A.error(log, "dashboard upload (write failed)",
                    {"session_id": session_id, "name": safe_name, "err": str(error)})
            A.state_file(log, state_path, "web-upload",
                         {"session_id": session_id, "name": safe_name,
                          "bytes": len(file_bytes),
                          "ok": False})
            return self._json({"error": "could not store upload"}, 500)
        A.state_file(log, state_path, "web-upload",
                     {"session_id": session_id, "name": safe_name,
                      "bytes": len(file_bytes),
                      "mime": mime, "ok": True})
        return self._json({"ok": True, "path": path, "name": safe_name,
                           "mime": mime, "is_image": is_image})

    def post_clipboard_files(self):
        """Resolve the FULL PATHS of files the browser just pasted as zero-byte
        promises (docs/dashboard.md *Web attachments* → *Promise-only files*).
        Body: {names: [basename, …], session_id?} → {paths: [abs, …]}.

        The page cannot answer this itself: a pasted `File` carries a BASENAME
        and nothing else (the web platform never exposes a filesystem path),
        while the pasteboard's path-bearing flavors are hidden from script. The
        server shares the pasteboard with kitty, so it reads what kitty reads —
        this endpoint is the whole reason the web composer can match the TUI.

        `clipboard.match` returns paths ONLY when their basenames are exactly
        what the caller reported, so a remote device (whose clipboard is not
        this Mac's) can never be handed an unrelated host path. A miss is a
        200 with `paths: []`, not an error — "the clipboard moved on" is an
        ordinary outcome and the page falls back to the bare name.

        Read-only apart from its audit row: nothing is written, nothing is
        staged, no terminal is touched. It stays behind _post_guard anyway —
        it reads local machine state on a page's say-so, which is exactly what
        the browser-vector defense is for."""
        body = self._post_guard()
        if body is None:
            return
        session_id = body.get("session_id")
        session_id = (
            session_id
            if isinstance(session_id, str) and valid_session_id(session_id)
            else ""
        )
        log, state_path = "", ""
        names = body.get("names")
        if not isinstance(names, list) or not names \
                or not all(isinstance(n, str) for n in names):
            return self._reject_input(
                "web-clipboard", "bad names", "names required",
                {"names": names}, log=log, path=state_path)
        names = [os.path.basename(n) for n in names[:clipboard.FILES_MAX]]
        paths = clipboard.match(names)
        # The paths ARE the diagnostic here ("it pasted the wrong file" is
        # otherwise unanswerable), and a mismatch records what was asked for so
        # a phone-vs-Mac clipboard divergence is visible as such.
        A.state_file(log, state_path, "web-clipboard",
                     {"session_id": session_id, "names": names, "matched": len(paths),
                      "paths": paths})
        return self._json({"paths": paths})

    def post_dictate_token(self):
        """Mint a short-lived Deepgram grant for the browser's DIRECT wss
        connection (docs/dashboard.md *Web dictation* — the stdlib server
        can't speak WebSocket and must never see audio, so its whole role is
        this trade: on-disk API key → ~30s single-purpose JWT). The response
        carries the token plus the fully-assembled listen URL (model +
        keyterms server-side; the client contributes only its AudioContext
        sample rate). Behind _post_guard like every control-plane POST — on
        READONLY days dictation is off exactly like the composer it feeds.
        Every attempt is a `web-dictate` state_files row (no session id — the
        new-session form dictates too), failures also an A.error. The API
        key never appears in a response or an audit row."""
        body = self._post_guard()
        if body is None:
            return
        rate = body.get("sample_rate")
        harness = body.get("harness")
        if not isinstance(rate, int) or isinstance(rate, bool) \
                or not (dictate.SAMPLE_RATE_MIN <= rate
                        <= dictate.SAMPLE_RATE_MAX):
            A.state_file("", "", "web-dictate",
                         {"ok": False, "why": "bad-rate",
                          "rate": repr(rate)[:40]})
            return self._json({"error": "bad sample_rate"}, 400)
        if not isinstance(harness, str) or not harness:
            return self._json({"error": "harness is required"}, 400)
        if not dictate.available():
            # a race fallback only — the page hides the mic button when the
            # /api/dictate probe says unavailable
            A.state_file("", "", "web-dictate", {"ok": False, "why": "no-key"})
            return self._json({"error": "no deepgram key configured"}, 501)
        # An omitted directory requests global terms. A supplied directory is
        # exact application input and must still exist.
        working_directory = body.get("working_directory")
        if working_directory is not None and (
            not isinstance(working_directory, str)
            or not os.path.isdir(working_directory)
        ):
            return self._json(
                {"error": "working_directory must be an existing directory"},
                400,
            )
        try:
            grant = dictate.grant()
        except Exception as error:
            A.error("", "dashboard dictate (grant failed)",
                    {"err": ("%s: %s" % (type(error).__name__, error))[:200]})
            A.state_file("", "", "web-dictate", {"ok": False, "why": "grant"})
            return self._json({"error": "token grant failed"}, 502)
        catalog = self._application().catalog.read(
            harness,
            QueryContext(session_id=None, working_directory=working_directory),
        )
        terms = dictate.keyterms(catalog.speech_terms)
        url = dictate.ws_url(rate, terms)
        A.state_file("", "", "web-dictate",
                     {"ok": True, "rate": rate, "working_directory": working_directory,
                      "keyterms": len(terms)})
        return self._json({"token": grant["access_token"],
                           "expires_in": grant.get("expires_in"),
                           "ws_url": url})
