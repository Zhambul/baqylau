# Copyright (c) 2026 Zhambyl Yermagambet
"""Replay a Codex hook prompt through storage and the HTTP feed."""

from http import HTTPStatus

from tests import http_test_assets, http_test_controls
from tests.e2e_replay.audit_replay_support import replay
from tests.harness_names import CODEX_HARNESS


# Harness limit: codex only. This wrapper is from a Codex rollout.
def test_hook_prompt_is_a_system_message() -> None:
    """Show a hook prompt as system text, not as user input."""
    application = replay("audit_codex_hook_prompt.jsonl", CODEX_HARNESS, "rollout")
    with http_test_assets.running_server(application) as server:
        response = http_test_controls.get(server, "/sessionData/session-one/entries")
        assert response.status == HTTPStatus.OK
        body = response.body.raw.decode()
        assert '"role":"system"' in body
        assert '"phase":"synthetic"' in body
        assert "Wiki persistence check" in body
        assert '"role":"user"' not in body
