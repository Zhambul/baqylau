# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one committed extension change frame through the HTTP stream."""

from __future__ import annotations

import http.client
from http import HTTPStatus
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
        connection = http.client.HTTPConnection(
            http_test_controls.LOOPBACK_ADDRESS, server.server_port, timeout=5,
        )
        connection.request("GET", f"/api/extensions/{OWNER}/changes?{query}")
        response = connection.getresponse()
        status = response.status
        content_type = response.getheader("Content-Type")
        lines: list[str] = []
        while len(lines) < FRAME_LINE_LIMIT:
            line = response.fp.readline().decode()
            if not line:
                break
            lines.append(line)
            if line == "\n" and any("event: changes" in row for row in lines):
                break
        connection.close()

    assert status == HTTPStatus.OK
    assert content_type is not None
    assert content_type.startswith("text/event-stream")
    assert any("event: changes" in row for row in lines)
    assert any('"note-0"' in row for row in lines)
