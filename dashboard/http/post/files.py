# dashboard/http/post/files.py — bytes and paths: staging a composer ATTACHMENT
# on disk and handing back the `@path` mention that delivers it, resolving the
# full paths of files pasted as zero-byte promises off the host pasteboard, and
# minting the short-lived Deepgram grant the browser dictates through.
import base64
import re
import binascii
import os
import uuid

from core import paths as P
from core.noaudit import load_audit
from dashboard import (clipboard, dictate)
from dashboard import config
from dashboard.config import (IMAGE_MIMES)
from dashboard.http.base import valid_sid

A = load_audit()


class _FilesMixin:
    """Attachment staging, clipboard path resolution, dictation grants."""

    def post_upload(self):
        """Stage a composer ATTACHMENT (an image/screenshot the browser pasted,
        dropped, or picked, or any other file) on disk, and hand back the
        ABSOLUTE path the composer will inject as an `@path` mention (post_message
        / post_new_session). Body: {sid?, name, mime, data(base64)}.

        Transport is JSON+base64, NOT multipart: it keeps the whole _post_guard
        browser-vector defense (same-origin + custom header + read-only switch)
        with no boundary parser to write; the price is a base64 envelope, which
        UPLOAD_MAX budgets for. The bytes land under paths.UPLOADS_DIR (durable
        ~/.claude, OUTSIDE any repo working tree so `git status` stays clean),
        in a per-session subdir; this mkdir is a sanctioned control-plane write
        (gated by _post_guard/READONLY like every other mutating POST).

        docs/dashboard.md, *Web attachments*."""
        body = self._post_guard(config.UPLOAD_MAX)
        if body is None:
            return
        sid = body.get("sid")
        sid = sid if isinstance(sid, str) and valid_sid(sid) else ""
        # Audit target: the uploader's session when we have a sid, else the
        # GLOBAL stream — an empty log/path, exactly like web-launch/ns-prefs.
        # NOT P.mirror_log(""), which is not a global key at all: with no sid it
        # falls back to the cwd SLUG of whatever directory the dashboard process
        # was started in, so a log-less staging upload used to file its rows in
        # the audit timeline of an unrelated session that happens to run in the
        # main checkout (the reject path already used "" — the success path did
        # not, and the two disagreed).
        log, sdb = self._audit_target(sid)[1:] if sid else ("", "")
        name = body.get("name")
        mime = body.get("mime") or ""
        data_b64 = body.get("data")
        if not isinstance(name, str) or not isinstance(data_b64, str) \
                or not isinstance(mime, str):
            return self._reject_input(
                "web-upload", "bad fields", "name, mime, data required",
                {"name": name, "mime": mime}, log=log, path=sdb)
        # basename only — strip any path component a hostile name carries, and
        # fall back to a neutral stem so an empty/dotfile name can't produce a
        # bare-uuid or hidden file.
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(name)).lstrip(".")
        safe = safe[:80] or "attachment"
        try:
            raw = base64.b64decode(data_b64, validate=True)
        except (binascii.Error, ValueError):
            return self._reject_input("web-upload", "bad base64",
                                      "invalid base64", {"name": safe},
                                      log=log, path=sdb)
        if not raw:
            return self._reject_input("web-upload", "empty file", "empty file",
                                      {"name": safe}, log=log, path=sdb)
        if len(raw) > config.UPLOAD_MAX:
            return self._reject_input("web-upload", "too large",
                                      "file too large", {"bytes": len(raw)},
                                      code=413, log=log, path=sdb)
        is_image = mime in IMAGE_MIMES
        dest_dir = P.session_uploads_dir(sid)
        path = os.path.join(dest_dir, "%s-%s" % (uuid.uuid4().hex[:8], safe))
        try:
            os.makedirs(dest_dir, exist_ok=True)
            with open(path, "wb") as f:
                f.write(raw)
        except OSError as e:
            A.error(log, "dashboard upload (write failed)",
                    {"sid": sid, "name": safe, "err": str(e)})
            A.state_file(log, sdb, "web-upload",
                         {"sid": sid, "name": safe, "bytes": len(raw),
                          "ok": False})
            return self._json({"error": "could not store upload"}, 500)
        A.state_file(log, sdb, "web-upload",
                     {"sid": sid, "name": safe, "bytes": len(raw),
                      "mime": mime, "ok": True})
        return self._json({"ok": True, "path": path, "name": safe,
                           "mime": mime, "is_image": is_image})

    def _attachment_paths(self, body):
        """The vetted `@path` prefix for the delivered message text, from a POST
        body's optional `attachments` list. Each entry MUST be an absolute path
        under paths.UPLOADS_DIR (so a caller can't smuggle an arbitrary
        filesystem path into an `@`-mention) and exist on disk. Returns a list of
        absolute paths (possibly empty); silently drops anything that fails the
        checks — the message still goes, just without the bad attachment."""
        raw = body.get("attachments")
        if not isinstance(raw, list):
            return []
        root = os.path.realpath(P.UPLOADS_DIR) + os.sep
        out = []
        for p in raw:
            if not isinstance(p, str) or not p:
                continue
            rp = os.path.realpath(p)
            if (rp + os.sep).startswith(root) and os.path.isfile(rp):
                out.append(rp)
        return out

    def _with_attachments(self, text, paths, host=None):
        """Prepend one MENTION token per attachment to the message text — the
        TUI-native way to attach a file, delivered verbatim over the existing
        paste_text / launch-argv transport. Paths first, then a newline, then the
        typed text (mirrors the TUI's paste-then-type order). No text is fine:
        the mentions alone are a valid message.

        The mention GRAMMAR is the receiving host's (`HostControl.mention` —
        claude_code's `@path`, which its TUI resolves and attaches). A host with
        no such grammar returns "" and gets the BARE PATH: it is still a file the
        model can open, where another tool's sigil would arrive as literal text
        and mean nothing (the codex leak in the P2 bug list, item 5). `host=None`
        keeps the historical `@path` for a caller that hasn't resolved one."""
        if not paths:
            return text
        mentions = " ".join((host.mention(p) if host is not None else "@" + p)
                            or p for p in paths)
        return mentions + ("\n" + text if text else "")

    def post_clipboard_files(self):
        """Resolve the FULL PATHS of files the browser just pasted as zero-byte
        promises (docs/dashboard.md *Web attachments* → *Promise-only files*).
        Body: {names: [basename, …], sid?} → {paths: [abs, …]}.

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
        sid = body.get("sid")
        sid = sid if isinstance(sid, str) and valid_sid(sid) else ""
        log, sdb = self._audit_target(sid)[1:] if sid else ("", "")
        names = body.get("names")
        if not isinstance(names, list) or not names \
                or not all(isinstance(n, str) for n in names):
            return self._reject_input(
                "web-clipboard", "bad names", "names required",
                {"names": names}, log=log, path=sdb)
        names = [os.path.basename(n) for n in names[:clipboard.FILES_MAX]]
        paths = clipboard.match(names)
        # The paths ARE the diagnostic here ("it pasted the wrong file" is
        # otherwise unanswerable), and a mismatch records what was asked for so
        # a phone-vs-Mac clipboard divergence is visible as such.
        A.state_file(log, sdb, "web-clipboard",
                     {"sid": sid, "names": names, "matched": len(paths),
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
        Every attempt is a `web-dictate` state_files row (no sid — the
        new-session form dictates too), failures also an A.error. The API
        key never appears in a response or an audit row."""
        body = self._post_guard()
        if body is None:
            return
        rate = body.get("sample_rate")
        if not isinstance(rate, int) or isinstance(rate, bool) \
                or not (dictate.SAMPLE_RATE_MIN <= rate
                        <= dictate.SAMPLE_RATE_MAX):
            A.state_file("", "", "web-dictate",
                         {"ok": False, "why": "bad-rate",
                          "rate": repr(rate)[:40]})
            return self._json({"error": "bad sample_rate"}, 400)
        if not dictate.available():
            # a race fallback only — the page hides the mic button when the
            # /api/dictate probe says unavailable
            A.state_file("", "", "web-dictate", {"ok": False, "why": "no-key"})
            return self._json({"error": "no deepgram key configured"}, 501)
        # optional cwd — keys the PROJECT vocabulary layer (the composer sends
        # its session's cwd, the new-session form its typed dir). A non-string
        # or non-directory degrades to global-only, never an error — the same
        # contract as /api/commands, and for the same reason (arbitrary
        # sessions' dirs come and go).
        cwd = body.get("cwd")
        if not isinstance(cwd, str) or not os.path.isdir(cwd):
            cwd = ""
        try:
            tok = dictate.grant()
        except Exception as e:
            A.error("", "dashboard dictate (grant failed)",
                    {"err": ("%s: %s" % (type(e).__name__, e))[:200]})
            A.state_file("", "", "web-dictate", {"ok": False, "why": "grant"})
            return self._json({"error": "token grant failed"}, 502)
        terms = dictate.keyterms(cwd)
        url = dictate.ws_url(rate, terms)
        A.state_file("", "", "web-dictate",
                     {"ok": True, "rate": rate, "cwd": cwd,
                      "keyterms": len(terms)})
        return self._json({"token": tok["access_token"],
                           "expires_in": tok.get("expires_in"),
                           "ws_url": url})
