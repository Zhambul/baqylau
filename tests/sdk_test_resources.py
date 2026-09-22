# Copyright (c) 2026 Zhambyl Yermagambet
"""Build SDK resources with test transports."""

from typing import cast

from sdk.client import (
    DiagnosticsResource,
    ExtensionsResource,
    SessionsResource,
    StreamsResource,
    TerminalResource,
    UploadsResource,
)
from sdk.transport import HttpTransport


def _transport(transport: object) -> HttpTransport:
    return cast("HttpTransport", transport)


def diagnostics_resource(transport: object) -> DiagnosticsResource:
    """Build a diagnostics resource.

    Returns:
        The resource with the given test transport.

    """
    return DiagnosticsResource(_transport(transport))


def extensions_resource(transport: object) -> ExtensionsResource:
    """Build an extensions resource.

    Returns:
        The resource with the given test transport.

    """
    return ExtensionsResource(_transport(transport))


def sessions_resource(transport: object) -> SessionsResource:
    """Build a session resource.

    Returns:
        The resource with the given test transport.

    """
    return SessionsResource(_transport(transport))


def streams_resource(transport: object) -> StreamsResource:
    """Build a stream resource.

    Returns:
        The resource with the given test transport.

    """
    return StreamsResource(_transport(transport))


def uploads_resource(transport: object) -> UploadsResource:
    """Build an upload resource.

    Returns:
        The resource with the given test transport.

    """
    return UploadsResource(_transport(transport))


def terminal_resource(transport: object) -> TerminalResource:
    """Build a terminal resource.

    Returns:
        The resource with the given test transport.

    """
    return TerminalResource(_transport(transport))
