# Copyright (c) 2026 Zhambyl Yermagambet
"""Check replies and context from Opus 4.6 records."""

from domain.ids import HarnessName, SessionId
from harness.impl.claude_code import plugin_info
from tests import http_test_assets, http_test_controls, http_test_pane_models
from tests.e2e_replay.audit_replay_support import replay
from tests.provider_graph import ProviderGraph

CONTEXT_TOKENS = 600
CONTEXT_WINDOW = 1_000_000


# Harness limit: claude_code only. These fields are from Claude records.
def test_opus_six_reply_and_context_reach_http() -> None:
    """Keep replies and usage when Claude adds wire metadata."""
    application = replay("audit_claude_opus_six.jsonl", HarnessName.CLAUDE_CODE, "transcript")
    _assert_translated(application)
    state = application.session_data.read(SessionId("session-one"))
    assert state is not None
    assert state.actors[0].context.used_tokens == CONTEXT_TOKENS
    assert state.actors[0].context.window_tokens == CONTEXT_WINDOW
    with http_test_assets.running_server(application) as server:
        response = http_test_controls.get(server, "/sessionData/session-one")
        assert '"model":"opus-4.6"' in response.body.raw.decode()
        response = http_test_controls.get(server, "/sessionData/session-one/entries")
        assert "The file is ready." in response.body.raw.decode()


def test_opus_six_is_selectable() -> None:
    """Offer the pinned model without changing the default."""
    assert "claude-opus-4-6" in plugin_info.MODEL_IDS


def _assert_translated(application: ProviderGraph) -> None:
    for audit in http_test_pane_models.raw_event_audits(application).audits_for_session(SessionId("session-one")):
        assert audit.interpretation is not None
        assert audit.interpretation.decision != "translation_failed"
