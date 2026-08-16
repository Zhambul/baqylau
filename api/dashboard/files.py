# api/dashboard/files.py — bytes and paths: staging a composer ATTACHMENT on
# disk and handing back the `@path` mention that delivers it, resolving the
# full paths of files pasted as zero-byte promises off the host pasteboard,
# and minting the short-lived Deepgram grant the browser dictates through.
from __future__ import annotations

import base64
import binascii
import os
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from api.config import IMAGE_MIMES
from api.dashboard.models.files.clipboard_files_request import ClipboardFilesRequest
from api.dashboard.models.files.clipboard_matches_response import (
    ClipboardMatchesResponse,
)
from api.dashboard.models.files.dictation_grant_response import DictationGrantResponse
from api.dashboard.models.files.dictation_token_request import DictationTokenRequest
from api.dashboard.models.files.upload_request import UploadRequest
from api.dashboard.models.files.upload_response import UploadResponse
from api.guard import control_plane, reject_input, valid_session_id
from diagnostics import record as A
from core.daemon.contract import UPLOAD_MAX
from core import clipboard
from dashboard import dictate, paths

router = APIRouter()

ATTACHMENT_NAME_LIMIT = 80
_UNSAFE_NAME_CHARACTERS = re.compile(r"[^A-Za-z0-9._-]")


def _claimed_session_id(value: str | None) -> str:
    return value if isinstance(value, str) and valid_session_id(value) else ""


@router.post("/api/application/uploads", dependencies=[Depends(control_plane(UPLOAD_MAX))])
def upload(body: UploadRequest) -> UploadResponse:
    """Stage a composer ATTACHMENT (an image/screenshot the browser pasted,
    dropped, or picked, or any other file) on disk, and hand back the ABSOLUTE
    path the composer will inject as an `@path` mention.

    Transport is JSON+base64, NOT multipart: it keeps the whole browser-vector
    defense (same-origin + custom header + read-only switch) with no boundary
    parser; the price is a base64 envelope, which UPLOAD_MAX budgets for. The
    bytes land under the application data directory, outside any repository
    working tree, in a per-session subdir."""
    session_id = _claimed_session_id(body.session_id)
    # basename only — strip any path component a hostile name carries, and
    # fall back to a neutral stem so an empty/dotfile name can't produce a
    # bare-uuid or hidden file.
    safe_name = _UNSAFE_NAME_CHARACTERS.sub("_", os.path.basename(body.name)).lstrip(".")
    safe_name = safe_name[:ATTACHMENT_NAME_LIMIT] or "attachment"
    try:
        file_bytes = base64.b64decode(body.data, validate=True)
    except (binascii.Error, ValueError):
        raise reject_input("web-upload", "bad base64", "invalid base64",
                           {"name": safe_name}) from None
    if not file_bytes:
        raise reject_input("web-upload", "empty file", "empty file", {"name": safe_name})
    if len(file_bytes) > UPLOAD_MAX:
        raise reject_input("web-upload", "too large", "file too large",
                           {"bytes": len(file_bytes)}, code=413)
    destination_directory = paths.session_uploads_directory(session_id)
    path = os.path.join(destination_directory, "%s-%s" % (uuid.uuid4().hex[:8], safe_name))
    try:
        os.makedirs(destination_directory, exist_ok=True)
        with open(path, "wb") as destination_file:
            destination_file.write(file_bytes)
    except OSError as error:
        A.error("", "dashboard upload (write failed)",
                {"session_id": session_id, "name": safe_name, "err": str(error)})
        A.state_file("", "", "web-upload",
                     {"session_id": session_id, "name": safe_name,
                      "bytes": len(file_bytes), "ok": False})
        # Raised, not returned: this route is declared to answer with an
        # UploadResponse, and every other rejection in it raises. _http_error
        # renders an HTTPException as the same {"error": ...} body at the same
        # status, so the wire response is unchanged.
        raise HTTPException(500, "could not store upload") from error
    A.state_file("", "", "web-upload",
                 {"session_id": session_id, "name": safe_name,
                  "bytes": len(file_bytes), "mime": body.mime, "ok": True})
    return UploadResponse(path=path, name=safe_name, mime=body.mime,
                          is_image=body.mime in IMAGE_MIMES)


@router.post("/api/application/clipboard-files", dependencies=[Depends(control_plane())])
def clipboard_files(body: ClipboardFilesRequest) -> ClipboardMatchesResponse:
    """Resolve the FULL PATHS of files the browser just pasted as zero-byte
    promises. The page cannot answer this itself: a pasted `File` carries a
    BASENAME and nothing else, while the pasteboard's path-bearing flavors are
    hidden from script. The server shares the pasteboard with the terminal, so
    it reads what the terminal reads. `clipboard.match` returns paths ONLY
    when their basenames are exactly what the caller reported, so a remote
    device can never be handed an unrelated host path. A miss is a 200 with
    `paths: []` — "the clipboard moved on" is an ordinary outcome."""
    session_id = _claimed_session_id(body.session_id)
    names = [os.path.basename(name) for name in body.names[:clipboard.FILES_MAX]]
    matched = clipboard.match(names)
    # The paths ARE the diagnostic here ("it pasted the wrong file" is
    # otherwise unanswerable), and a mismatch records what was asked for so a
    # phone-vs-host clipboard divergence is visible as such.
    A.state_file("", "", "web-clipboard",
                 {"session_id": session_id, "names": names, "matched": len(matched),
                  "paths": matched})
    return ClipboardMatchesResponse(paths=tuple(matched))


@router.post("/api/application/dictation-token", dependencies=[Depends(control_plane())])
def dictation_token(body: DictationTokenRequest):
    """Mint a short-lived Deepgram grant for the browser's DIRECT wss
    connection (this server never sees audio; its whole role is this trade:
    on-disk API key → ~30s single-purpose JWT). The mic is always offered —
    there is no availability probe — so a missing key surfaces HERE, as the
    501 the page toasts. Every attempt is a `web-dictate` state_files row (no
    session id — the new-session form dictates too); the API key never
    appears in a response or an audit row."""
    if not (dictate.SAMPLE_RATE_MIN <= body.sample_rate <= dictate.SAMPLE_RATE_MAX):
        A.state_file("", "", "web-dictate",
                     {"ok": False, "why": "bad-rate", "rate": repr(body.sample_rate)[:40]})
        return JSONResponse({"error": "bad sample_rate"}, 400)
    if not dictate.available():
        A.state_file("", "", "web-dictate", {"ok": False, "why": "no-key"})
        return JSONResponse({"error": "no deepgram key configured"}, 501)
    # An omitted directory requests global terms. A supplied directory is
    # exact application input and must still exist.
    if body.working_directory is not None and not os.path.isdir(body.working_directory):
        return JSONResponse({"error": "working_directory must be an existing directory"}, 400)
    try:
        grant = dictate.grant()
    except Exception as error:
        A.error("", "dashboard dictate (grant failed)",
                {"err": ("%s: %s" % (type(error).__name__, error))[:200]})
        A.state_file("", "", "web-dictate", {"ok": False, "why": "grant"})
        return JSONResponse({"error": "token grant failed"}, 502)
    terms = dictate.keyterms()
    A.state_file("", "", "web-dictate",
                 {"ok": True, "rate": body.sample_rate,
                  "working_directory": body.working_directory, "keyterms": len(terms)})
    return DictationGrantResponse(token=grant["access_token"],
                                  expires_in=grant.get("expires_in"),
                                  ws_url=dictate.ws_url(body.sample_rate, terms))
