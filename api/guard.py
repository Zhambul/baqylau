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

from diagnostics import record as A
from core.daemon.contract import POST_HEADER, POST_MAX
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
        # The cap is checked BEFORE the body is read, which is what makes it
        # free — and which is why the declared length has to exist. A request
        # that omits Content-Length (a chunked upload) once declared zero and
        # passed every cap, and the handler behind it then buffered the whole
        # stream: h11 imposes no maximum of its own, so the header WAS the limit
        # and an absent header was no limit at all. With one required, h11
        # delivers at most the length checked here, so the cap binds the bytes.
        # Every client of this server sends it (http.client and fetch both do,
        # for any body they are given).
        declared = request.headers.get("Content-Length")
        if declared is None:
            raise reject(request, 411, "content-length is required")
        try:
            length = int(declared)
        except ValueError:
            raise reject(request, 411, "content-length is not a number") from None
        if length < 0 or length > maximum_bytes:
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
