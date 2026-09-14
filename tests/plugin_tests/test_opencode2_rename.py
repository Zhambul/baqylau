# Copyright (c) 2026 Zhambyl Yermagambet
"""Require the native rename dialog and a new title event."""

from pathlib import Path

import pytest

from domain import ids
from harness.impl.opencode2.rename import RenameSessionHandler
from harness.models import controls
from harness.models.session import Session
from tests.fake_terminal import FakeTerminal
from tests.plugin_tests.support_controls import control_context

RECORDED_TITLE = "Request to output ACTIVITY_DONE marker"


@pytest.mark.parametrize("screen", ["Rename session\nenter submit", "Unsaved composer draft"])
def test_rename_needs_a_dialog_and_new_event(tmp_path: Path, screen: str) -> None:
    """An old matching title cannot confirm a new request."""
    fixture = Path(__file__).parents[1] / "e2e/fixtures/audit_opencode2_context.jsonl"
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(fixture.read_text())
    session = Session(
        ids.SessionId("session-one"), ids.ActorId("session-one:lead"), str(transcript), str(tmp_path),
    )
    terminal = FakeTerminal(screen_text=screen)
    outcome = RenameSessionHandler()(
        controls.RenameSession(session.session_id, ids.RequestId("new-rename"), RECORDED_TITLE),
        control_context(session, terminal.plugin()),
    )
    assert outcome.status == controls.ControlAcknowledgement.INDETERMINATE
    assert bool(terminal.submitted) == ("Rename session" in screen)
