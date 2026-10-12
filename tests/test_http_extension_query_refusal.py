# Copyright (c) 2026 Zhambyl Yermagambet
"""Answer a worker that refuses a declared query as unavailable, not as an internal error."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.runtime.models import ExtensionTransportError

from domain.ids import SessionId
from tests import http_test_controls, http_test_server_runtime
from tests.extension_api import peer_example
from tests.http_test_extension_queries import QUERY_ID, QUERY_PATH, query_body, query_server

if TYPE_CHECKING:
    import pytest
    from baqylau_extension_api.models import queries

    from tests.http_test_pane_models import RunningDaemon

UNAVAILABLE_STATUS = 503
# The worker's own words when its live lane already holds the most calls it accepts.
LANE_FULL = "extension execution lane is closed or full"


def _refuse(_plugin: object, _query_request: queries.QueryRequest) -> queries.QueryResult:
    raise ExtensionTransportError(LANE_FULL)


def _status(server: RunningDaemon, body: dict[str, http_test_server_runtime.JsonValue]) -> int:
    status, _ = http_test_controls.post(server, QUERY_PATH, body)
    return status


def test_busy_worker_query_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer each refused call with 503, and record a burst as one audit row with its traceback."""
    monkeypatch.setattr(peer_example.PeerExample, "query", _refuse)
    application = http_test_server_runtime.application()
    body = query_body()

    with query_server(application) as server:
        statuses = {_status(server, body) for _ in range(3)}

    errors = application.audit_reads.errors_for_session(SessionId(""))
    assert statuses == {UNAVAILABLE_STATUS}
    assert [error.action for error in errors] == [f"extension query ({QUERY_ID})"]
    assert LANE_FULL in errors[0].traceback
