# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep current input fields through translation and the HTTP response."""

from http import HTTPStatus

import pytest

from domain.ids import HarnessName, SessionId
from repository.impl.sqlite.audit_read import SqliteAuditReadRepository
from tests import http_test_assets, http_test_controls
from tests.e2e_replay.audit_replay_support import replay
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS


# Harness limit: claude_code only. These fields are from Claude records.
@pytest.mark.parametrize(
    ("filename", "route", "expected"),
    [
        ("audit_claude_metadata.jsonl", "/entries", "browser-result-marker"),
        ("audit_claude_goal.jsonl", "", "audit-goal-marker"),
    ],
)
def test_claude_audit_fields_reach_http(filename: str, route: str, expected: str) -> None:
    """Accept each recorded field and expose its event through HTTP."""
    _assert_fields(filename, CLAUDE_CODE_HARNESS, "transcript", route, expected)


# Harness limit: codex only. The error field is from a Codex record.
def test_codex_limit_reaches_http() -> None:
    """Accept the usage-limit field and show the end of the turn."""
    _assert_fields("audit_codex_limit.jsonl", CODEX_HARNESS, "rollout", "/entries", "turn_finished")


def _assert_fields(filename: str, harness: HarnessName, source_type: str, route: str, expected: str) -> None:
    application = replay(filename, harness, source_type)
    audit_reads = application.provider("audit_reads", SqliteAuditReadRepository)
    assert audit_reads.errors_for_session(SessionId("session-one")) == ()
    with http_test_assets.running_server(application) as server:
        response = http_test_controls.get(server, f"/sessionData/session-one{route}")
        assert response.status == HTTPStatus.OK
        assert expected in response.body.raw.decode()
