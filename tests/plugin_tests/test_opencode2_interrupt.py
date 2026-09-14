# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep old interruption records from confirming a new request."""

from pathlib import Path

from domain import ids
from harness.impl.opencode2.interrupt import InterruptHandler
from harness.models import controls
from harness.models.session import Session
from tests.fake_terminal import FakeTerminal
from tests.plugin_tests.support_controls import control_context

EXPECTED_INTERRUPT_KEYS = 2


def test_old_interrupt_does_not_confirm_request(tmp_path: Path) -> None:
    """Successful key writes need a new native interruption record."""
    fixture = Path(__file__).parents[1] / "e2e/fixtures/audit_opencode2_interrupt.jsonl"
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(fixture.read_text())
    session = Session(
        ids.SessionId("session-one"), ids.ActorId("session-one:lead"), str(transcript), str(tmp_path),
    )
    terminal = FakeTerminal()
    outcome = InterruptHandler()(
        controls.Interrupt(session.session_id, ids.RequestId("new-interrupt")),
        control_context(session, terminal.plugin(), lead_active=True),
    )
    assert outcome.status == controls.ControlAcknowledgement.INDETERMINATE
    assert not outcome.corroborated
    assert len(terminal.keys) == EXPECTED_INTERRUPT_KEYS
