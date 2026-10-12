# Copyright (c) 2026 Zhambyl Yermagambet
"""Low-level HTTP exchanges for the daemon client."""

from __future__ import annotations

from http import HTTPStatus, client as http_client
from typing import TYPE_CHECKING

import _http

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

CONTENT_TYPE_JSON = "application/json"
EVENT_STREAM = "text/event-stream"


def connection(host: str, port: int, timeout: float) -> http_client.HTTPConnection:
    """Create one connection to the configured local daemon.

    Returns:
        An HTTP connection with the selected address and timeout.

    """
    selected_host = host or _http.HOST
    selected_port = port or _http.PORT
    return http_client.HTTPConnection(selected_host, selected_port, timeout=timeout)


def post_exchange(
    active_connection: http_client.HTTPConnection,
    path: str,
    body: bytes,
    headers: Mapping[str, str] | None,
) -> tuple[int, bytes]:
    """Send one POST request.

    Returns:
        The HTTP status and complete response body.

    """
    request_headers = {"Content-Type": CONTENT_TYPE_JSON, **(headers or {})}
    active_connection.request("POST", path, body, request_headers)
    response = active_connection.getresponse()
    return response.status, response.read()


def get_exchange(active_connection: http_client.HTTPConnection, path: str) -> tuple[int, bytes]:
    """Send one GET request.

    Returns:
        The HTTP status and complete response body.

    """
    active_connection.request("GET", path)
    response = active_connection.getresponse()
    return response.status, response.read()


def stream_lines(active_connection: http_client.HTTPConnection, path: str) -> Iterator[str]:
    """Read decoded response lines from one streaming GET request.

    Yields:
        Response lines without trailing newline characters.

    Raises:
        OSError: If the server does not return HTTP 200.

    """
    # The daemon sends an event stream's headers at once, and does not compress it, only for this accept type.
    request_headers = {"Accept": EVENT_STREAM}
    active_connection.request("GET", path, headers=request_headers)
    response = active_connection.getresponse()
    if response.status != HTTPStatus.OK:
        message = f"stream refused: {int(response.status)}"
        raise OSError(message)
    for raw_line in response:
        yield raw_line.decode("utf-8", "replace").rstrip("\n")
