# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep a cancelled question from blocking its session for ever."""

from http import HTTPStatus
from pathlib import Path

from domain.ids import SessionId
from tests import http_test_assets, http_test_preferences
from tests.e2e_replay import opencode2_support

SESSION = "ses_cancelledQuestion"
FIXTURE = Path(__file__).parents[1] / "e2e/fixtures/audit_opencode2_cancelled_question.jsonl"


# Harness limit: no harness. Replay a captured shape through the real hook endpoint and file reader.
def test_cancelled_question_frees_its_session(tmp_path: Path) -> None:
    """A question whose tool join was lost must not outlive its aborted turn.

    The plugin joins a tool to its result in memory. A reload can lose that
    join, and the captured failed call then names no tool: nothing translates
    the aborted question into an answer. Its turn end must resolve it instead.
    """
    source = FIXTURE.read_text().replace("session-one", SESSION)
    (tmp_path / f"{SESSION}.jsonl").write_text(source)
    application = opencode2_support.application(tmp_path)
    with http_test_assets.running_server(application) as server:
        assert http_test_preferences.post_hook(
            server, "opencode2", source.splitlines()[-1].encode(),
            {"X-Baqylau-Terminal-Window": opencode2_support.WINDOW},
        )[0] == HTTPStatus.OK
        opencode2_support.drain(application)
    session_id = SessionId(SESSION)
    state = application.session_data.read(session_id)
    assert state is not None
    # The write finished inside the running turn: finished work must not
    # repaint an actor whose question died with the turn that asked it.
    assert state.actors[0].status == "working"
    assert state.actors[0].pending_attention_internal == ()
    assert application.session_data.pending_attention(session_id) == ()
