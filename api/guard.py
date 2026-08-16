# api/guard.py — the control-plane POST guard, as a FastAPI dependency.
#
# The browser-vector defense every mutating endpoint sits behind. Checked in
# order: the read-only kill switch, the content type, the Origin allowlist,
# the caller proof, the body-size cap. Two accepted proofs of a same-origin
# caller, EITHER suffices:
#   * the `X-Baqylau` custom header (a cross-origin *simple* POST can't set it,
#     and a cross-origin fetch that tries triggers a preflight this no-CORS
#     server never answers), OR
#   * a present-and-allowlisted `Origin` — a cross-origin page cannot forge an
#     allowlisted Origin, and the browser stamps the *real* Origin on every
#     cross-origin request, so the Origin allowlist IS the CSRF gate here; the
#     header is defence-in-depth. A non-allowlisted Origin is always rejected.
#
# There is deliberately NO CORS middleware anywhere in api/: never answering a
# preflight is part of the defense.
from fastapi import HTTPException, Request

from core import audit as A
from core.wire import POST_HEADER, POST_MAX
from api import config

CONTENT_TYPE_JSON = "application/json"


def reject(request: Request, code: int, why: str) -> HTTPException:
    """One guard rejection: audited as a `web-reject` state_files row (path =
    the rejected request path, content = code + reason) — the ONE place a
    control-plane POST could vanish without a trace, since the guard rejects
    BEFORE any handler runs. Audit-only telemetry (not an `errors` row — an
    expected 4xx), so it never lights the errwatch chip."""
    A.state_file("", request.url.path[:200], "web-reject", {"code": code, "why": why})
    return HTTPException(code, why)


def control_plane(maximum_bytes: int = POST_MAX):
    """The guard for one route, parameterized by its body cap. Use as
    `Depends(control_plane())`; the upload and hook-delivery routes pass their
    own caps. Runs before the body is parsed, so a rejected request costs no
    validation work."""

    def guard(request: Request) -> None:
        if config.READONLY:
            raise reject(request, 403, "control plane disabled (read-only)")
        content_type = (request.headers.get("Content-Type") or "").split(";")[0].strip()
        if content_type != CONTENT_TYPE_JSON:
            raise reject(request, 415, "content-type must be application/json")
        origin = request.headers.get("Origin")
        if origin and origin not in config.ALLOWED_ORIGINS:
            raise reject(request, 403, "cross-origin")
        if request.headers.get(POST_HEADER) != "1" and origin not in config.ALLOWED_ORIGINS:
            raise reject(request, 403, "missing %s header" % POST_HEADER)
        try:
            declared = int(request.headers.get("Content-Length") or 0)
        except ValueError:
            declared = -1
        if declared < 0 or declared > maximum_bytes:
            raise reject(request, 413, "body too large")

    return guard


def reject_input(action, why, message, detail, code=400, log="", path=""):
    """Audit and reject malformed application input (the file routes' audited
    400s — a `state_files` row first, then the HTTP error)."""
    A.state_file(log, path, action,
                 dict({"ok": False, "why": why},
                      **{key: repr(value) for key, value in detail.items()}))
    return HTTPException(code, message)


def valid_session_id(value):
    return bool(config.SESSION_ID_PATTERN.match(value or ""))
