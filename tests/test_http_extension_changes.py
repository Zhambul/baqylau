# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one committed extension change frame through the HTTP stream."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.client import HTTPConnection
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from tests import http_test_assets, http_test_controls, http_test_server_runtime
from tests.test_http_extension_records import OWNER, SCOPE, seed_records

if TYPE_CHECKING:
    from pathlib import Path

FRAME_LINE_LIMIT = 30


def test_change_route_streams_a_committed_frame(tmp_path: Path) -> None:
    """The change route streams the first committed record boundary."""
    seed_records(tmp_path)
    query = urlencode({"scope": SCOPE.model_dump_json(), "cursor": 0})

    with http_test_assets.running_server(http_test_server_runtime.application()) as server:
        response = _first_frame(server.server_port, f"/api/extensions/{OWNER}/changes?{query}")

    assert response.status == HTTPStatus.OK
    assert response.content_type is not None
    assert response.content_type.startswith("text/event-stream")
    assert any("event: changes" in row for row in response.lines)
    assert any('"note-0"' in row for row in response.lines)


@dataclass(frozen=True)
class _StreamStart:
    status: int
    content_type: str | None
    lines: tuple[str, ...]


def _first_frame(port: int, path: str) -> _StreamStart:
    """Read the stream lines until the first change frame ends.

    Returns:
        The status, the content type, and the lines that were read.

    """
    connection = HTTPConnection(http_test_controls.LOOPBACK_ADDRESS, port, timeout=5)
    connection.request("GET", path)
    response = connection.getresponse()
    lines: list[str] = []
    while len(lines) < FRAME_LINE_LIMIT:
        line = response.fp.readline().decode()
        if not line:
            break
        lines.append(line)
        if line == "\n" and any("event: changes" in row for row in lines):
            break
    connection.close()
    return _StreamStart(response.status, response.getheader("Content-Type"), tuple(lines))
