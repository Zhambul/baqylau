# Copyright (c) 2026 Zhambyl Yermagambet
"""Carry host call correlation without exposing authority in feature requests."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from time import monotonic

from pydantic import TypeAdapter

from baqylau_extension_api.models.base import Identifier
from baqylau_extension_api.runtime.models import ExtensionTransportError, RequestTimeout

HOST_CALL_ID: ContextVar[str | None] = ContextVar("extension_host_call_id", default=None)
HOST_CALL_EXPIRY: ContextVar[float | None] = ContextVar("extension_host_call_expiry", default=None)


def current_host_call_id() -> str | None:
    """Read the host-issued reference for this executor call.

    Returns:
        A correlation reference, not proof of permission or an active job.

    """
    return HOST_CALL_ID.get()


@contextmanager
def host_call_scope(call_id: str | None, *, expires_at: float | None = None) -> Iterator[None]:
    """Keep one call reference local to its task and restore the prior context.

    Yields:
        Nothing; the host must verify this reference against its active call store.

    """
    checked = call_id
    if checked is not None:
        checked = TypeAdapter(Identifier).validate_python(checked, strict=True)
    if expires_at is not None:
        expires_at = TypeAdapter(RequestTimeout).validate_python(expires_at, strict=True)
    token = HOST_CALL_ID.set(checked)
    expiry_token = HOST_CALL_EXPIRY.set(expires_at)
    try:
        yield
    finally:
        HOST_CALL_ID.reset(token)
        HOST_CALL_EXPIRY.reset(expiry_token)


def remaining_call_seconds(default: float | None) -> float | None:
    """Bound transport waits by the host's current monotonic call deadline.

    Returns:
        The shorter remaining host limit or configured transport timeout.

    Raises:
        ExtensionTransportError: If the host call deadline has already passed.

    """
    expiry = HOST_CALL_EXPIRY.get()
    if expiry is None:
        return default
    remaining = expiry - monotonic()
    if default is not None:
        remaining = min(default, remaining)
    if remaining <= 0:
        message = "host extension call deadline has passed"
        raise ExtensionTransportError(message)
    return remaining
