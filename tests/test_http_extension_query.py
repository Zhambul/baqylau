# Copyright (c) 2026 Zhambyl Yermagambet
"""Run one declared extension query through the HTTP route."""

from __future__ import annotations

from tests import http_test_controls
from tests.http_test_extension_queries import OWNER, QUERY_PATH, query_body, query_server

OK_STATUS = 200
BAD_REQUEST_STATUS = 400


def test_query_route_returns_typed_result() -> None:
    """The query route resolves the active package and returns its typed result."""
    with query_server() as server:
        status, response = http_test_controls.post(server, QUERY_PATH, query_body())

    document = response.json
    assert status == OK_STATUS
    assert document["status"] == "ready"
    assert document["binding"]["extension_id"] == OWNER


def test_query_route_rejects_an_undeclared_query() -> None:
    """A query id the active package never declared is a client error."""
    missing = f"/api/extensions/{OWNER}/queries/{OWNER}.missing"
    with query_server() as server:
        status, _ = http_test_controls.post(server, missing, query_body())

    assert status == BAD_REQUEST_STATUS
