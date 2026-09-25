# Copyright (c) 2026 Zhambyl Yermagambet
"""A Claude rename whose draft cannot be cleared is rejected."""

from pathlib import Path

import pytest

from harness.impl.claude_code import probe
from harness.models import controls as control_models
from harness.models.session import Session
from tests.fake_terminal import FakeTerminal
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import (
    control_basic_support,
    control_driver_support,
    control_state_values,
    support_controls,
    vocabulary as fixture,
)

CLEAR_FAILURE = "the Claude composer did not contain the expected draft"


def _refuse_clear(*_arguments: object) -> None:
    raise probe.ComposerError(CLEAR_FAILURE)


def test_rename_with_uncleared_draft_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The rename is not typed, and the control answers with the reason and no server error."""
    source = control_basic_support.session_event_source(tmp_path)
    source.write_text("", encoding=fixture.TEXT_ENCODING)
    monkeypatch.setattr(probe.ClaudeCodeComposer, "clear", _refuse_clear)
    session = Session(
        control_state_values.PRIMARY_SESSION,
        control_state_values.PRIMARY_ACTOR,
        control_basic_support.source_name(source),
        str(tmp_path),
    )
    request = control_models.RenameSession(
        session_id=session.session_id,
        request_id=control_state_values.PRIMARY_REQUEST,
        name="A new title",
    )

    outcome = control_driver_support.controller(CLAUDE_CODE_HARNESS).execute(
        request,
        support_controls.control_context(
            session,
            FakeTerminal(screen_text=support_controls.claude_composer_screen()).plugin(),
        ),
    )

    assert isinstance(outcome, control_models.ControlResult)
    assert (outcome.status, outcome.reason) == (fixture.REJECTED, CLEAR_FAILURE)
