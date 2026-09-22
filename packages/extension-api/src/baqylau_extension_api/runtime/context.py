# Copyright (c) 2026 Zhambyl Yermagambet
"""Identify pure capability work when it tries to use a live host service."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from baqylau_extension_api.runtime.models import ExtensionTransportError

PURE_PROCESSING: ContextVar[bool] = ContextVar("extension_pure_processing", default=False)


@contextmanager
def processing_scope(*, pure: bool) -> Iterator[None]:
    """Apply a pure-work marker only for the current executor call."""
    token = PURE_PROCESSING.set(pure)
    try:
        yield
    finally:
        PURE_PROCESSING.reset(token)


def require_live_call() -> None:
    """Reject SDK service access from pure transforms and projections.

    Raises:
        ExtensionTransportError: If pure code tries to make a live call.

    """
    if PURE_PROCESSING.get():
        message = "pure extension processing cannot call live host services"
        raise ExtensionTransportError(message)
