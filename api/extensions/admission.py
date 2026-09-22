# Copyright (c) 2026 Zhambyl Yermagambet
"""Admit catalog writes from same-origin JSON requests or local API clients."""

from http import HTTPStatus

from fastapi import HTTPException, Request

from app.provider_extension_controls import ControlPolicy
from extensions.control_policy import require_extension_write


def require_catalog_write(request: Request, policy: ControlPolicy) -> None:
    """Apply the host write policy before a same-origin catalog mutation."""
    require_extension_write(policy)
    require_extension_json(request)


def require_extension_json(request: Request) -> None:
    """Reject cross-origin browser actions and simple form requests.

    Raises:
        HTTPException: If the origin or content type does not match this route.

    """
    origin = request.headers.get("origin")
    if origin is not None and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(HTTPStatus.FORBIDDEN, "extension requests require the same origin")
    content_type = request.headers.get("content-type", "")
    media_type = content_type.partition(";")[0].strip().lower()
    if media_type != "application/json":
        raise HTTPException(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "extension requests require application/json")
