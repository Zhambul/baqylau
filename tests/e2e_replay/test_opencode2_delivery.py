# Copyright (c) 2026 Zhambyl Yermagambet
"""Recover the native log after a registration event arrives."""

from http import HTTPStatus
from pathlib import Path

from domain.ids import SessionId
from tests import http_test_assets, http_test_controls, http_test_preferences
from tests.e2e_replay import opencode2_support

SESSION = "ses_baqylauE2E"


# Harness limit: no harness. Replay native records through the real hook endpoint and file reader.
def test_completion_hook_recovers_message_order(tmp_path: Path) -> None:
    """A late registration must not leave a completed turn marked as working."""
    source = (Path(__file__).parents[1] / "e2e/fixtures/audit_opencode2_greeting.jsonl").read_text()
    source = source.replace("session-one", SESSION)
    (tmp_path / f"{SESSION}.jsonl").write_text(source)
    application = opencode2_support.application(tmp_path)
    with http_test_assets.running_server(application) as server:
        assert http_test_preferences.post_hook(
            server, "opencode2", source.splitlines()[-1].encode(),
            {"X-Baqylau-Terminal-Window": opencode2_support.WINDOW},
        )[0] == HTTPStatus.OK
        opencode2_support.drain(application)
        assert '"text":"Hi"' in http_test_controls.get(
            server, f"/sessionData/{SESSION}/entries",
        ).body.raw.decode()
    state = application.session_data.read(SessionId(SESSION))
    assert state is not None
    assert state.actors[0].status == "awaiting_response"
