# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep native completion records that have no static JavaScript call."""

from domain.ids import SessionId
from tests import http_test_assets, http_test_controls, http_test_pane_models
from tests.e2e_replay.audit_replay_support import replay
from tests.harness_names import CODEX_HARNESS


# Harness limit: codex only. Dynamic tool lookup has no static call identity.
def test_dynamic_mcp_completion_is_accepted() -> None:
    """Accept a native completion and continue to the next message."""
    application = replay("audit_codex_dynamic_completion.jsonl", CODEX_HARNESS, "rollout")
    for audit in http_test_pane_models.raw_event_audits(application).audits_for_session(SessionId("session-one")):
        assert audit.interpretation is not None
        assert audit.interpretation.decision != "translation_failed"
    with http_test_assets.running_server(application) as server:
        response = http_test_controls.get(server, "/sessionData/session-one/entries")
        assert "Ready to continue" in response.body.raw.decode()
