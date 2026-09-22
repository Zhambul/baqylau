# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the bounded raw-event audit route."""

from http import HTTPStatus
from pathlib import Path

from tests import http_test_assets, http_test_controls, http_test_server_runtime
from tests.extension_host import interpretation_audit_steps_fixture as audit_fixture
from tests.extension_host.interpretation_large_fixture import large_applied_step

BOUNDED_AUDIT_BYTES = 16_384
MISSING_RAW_EVENT_ID = "raw-missing"


def test_audit_route_reports_bounded_steps(tmp_path: Path) -> None:
    """Verify the audit route reports bounded steps without stored bodies."""
    case = audit_fixture.step_case(tmp_path / "data", large_applied_step())

    with http_test_assets.running_server(http_test_server_runtime.application()) as server:
        response = http_test_controls.get(server, f"/api/diagnostics/raw-events/{case.raw_event_id}")

    document = response[2].json
    assert response[0] == HTTPStatus.OK
    assert document["steps"][0]["operation_kinds"] == ["insert"]
    assert len(response[2].raw) < BOUNDED_AUDIT_BYTES


def test_audit_route_reports_unknown_event(tmp_path: Path) -> None:
    """Verify the audit route rejects an unknown raw event."""
    audit_fixture.step_case(tmp_path / "data", audit_fixture.applied_step())

    with http_test_assets.running_server(http_test_server_runtime.application()) as server:
        response = http_test_controls.get(server, f"/api/diagnostics/raw-events/{MISSING_RAW_EVENT_ID}")

    assert response[0] == HTTPStatus.NOT_FOUND
