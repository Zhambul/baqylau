# client/_daemon.py — the whole transport, shared by every client here.
#
# `http.client`, not `urllib.request`: measured 43 ms against 50 ms of total
# process lifetime for a hook that does nothing else (the interpreter floor is
# 23 ms), and a POST to a fixed local port needs nothing urllib adds.
#
# A failure never leaves this module. A client that cannot reach the daemon does
# NOTHING — no debug row, no fallback store, no retry — because it must never
# fail the harness or the terminal that launched it, and because the daemon is
# the one interpreter: a delivery it never accepted did not happen. The single
# exception is `lines()`, whose caller reconnects for a living.
from __future__ import annotations

from collections.abc import Iterator, Mapping
import http.client
from typing import Protocol

import _http

# Hooks are local, but a busy workstation can briefly deschedule both the
# harness client and its daemon while many sessions start together. Keep the
# bound finite without dropping canonical events during that startup burst.
TIMEOUT_SECONDS = 5.0
CONTENT_TYPE_JSON = "application/json"


class JsonDocument(Protocol):
    def json_bytes(self) -> bytes: ...


def _connection(host: str, port: int, timeout: float) -> http.client.HTTPConnection:
    return http.client.HTTPConnection(host or _http.HOST, port or _http.PORT, timeout=timeout)


def post(
    path: str,
    body: bytes,
    headers: Mapping[str, str] | None = None,
    host: str = "",
    port: int = 0,
    timeout: float = TIMEOUT_SECONDS,
) -> bytes | None:
    """POST exact bytes. The reply bytes on 200, else None — which every caller
    treats as "nothing happened"."""
    connection = _connection(host, port, timeout)
    try:
        request_headers = {
            "Content-Type": CONTENT_TYPE_JSON,
            **(headers or {}),
        }
        connection.request("POST", path, body, request_headers)
        response = connection.getresponse()
        payload = response.read()
        return payload if response.status == 200 else None
    except (OSError, http.client.HTTPException, UnicodeError):
        return None
    finally:
        connection.close()


def post_json(
    path: str,
    document: JsonDocument,
    host: str = "",
    port: int = 0,
    timeout: float = TIMEOUT_SECONDS,
) -> bytes | None:
    return post(
        path,
        document.json_bytes(),
        host=host,
        port=port,
        timeout=timeout,
    )


def get(
    path: str,
    host: str = "",
    port: int = 0,
    timeout: float = TIMEOUT_SECONDS,
) -> bytes | None:
    """GET one resource. Its bytes on 200, else None."""
    connection = _connection(host, port, timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        payload = response.read()
        return payload if response.status == 200 else None
    except (OSError, http.client.HTTPException, UnicodeError):
        return None
    finally:
        connection.close()


def lines(path: str, host: str, port: int, timeout: float) -> Iterator[str]:
    """The decoded lines of one streaming GET.

    Raises OSError to its caller: a stream client's reconnect loop is the one
    place a failure is not silence, because reconnecting IS its job.
    """
    connection = _connection(host, port, timeout)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        if response.status != 200:
            raise OSError("stream refused: %d" % response.status)
        for raw_line in response:
            yield raw_line.decode("utf-8", "replace").rstrip("\n")
    except http.client.HTTPException as error:
        raise OSError(str(error)) from error
    finally:
        connection.close()
