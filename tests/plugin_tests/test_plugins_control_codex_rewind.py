# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness canonical translation tests from native fixture shapes."""

from pathlib import Path

import pytest

from domain import (
    ids as domain_ids,
)
from harness.impl.codex.controls import controller as codexcontroller, controller_send_state, controller_timeouts
from harness.models import controls as control_models
from harness.models.session import (
    Session,
)
from tests.fake_terminal import FakeTerminal
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import (
    control_basic_support,
    control_driver_support,
    control_rewind_support,
    control_state_values,
    support_controls,
    support_values,
    vocabulary as fixture,
)


def test_codex_idle_send_follows_new_rewind(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify codex idle send follows a new rewind rollout."""
    control_fixture = control_rewind_support.rewind_control_fixture(monkeypatch, tmp_path)
    codexcontroller.rewind_continuity.expect(
        control_fixture.session.session_id,
        control_state_values.PRIMARY_WINDOW,
    )

    outcome = support_values.controller_of(control_fixture.plugin).execute(
        control_models.SendText(
            control_fixture.session.session_id,
            domain_ids.RequestId(fixture.REQUEST_ONE_ID),
            text="revised",
        ),
        support_controls.control_context(
            control_fixture.session,
            control_fixture.terminal.plugin(),
        ),
    )

    assert isinstance(outcome, control_models.MessageDeliveryResult)
    assert outcome.status == fixture.SENT
    assert control_fixture.terminal.inserted == [(fixture.WINDOW_ONE_ID, "revised", "paste")]
    assert control_fixture.terminal.keys[-1] == (fixture.WINDOW_ONE_ID, fixture.ENTER)


def test_codex_idle_send_reports_missing_native(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify codex idle send reports missing native confirmation."""
    source = tmp_path / fixture.ROLLOUT_JSONL_PATH
    source.write_text("", encoding=fixture.TEXT_ENCODING)
    monkeypatch.setattr(controller_timeouts, "SEND_CONFIRM_TIMEOUT_SECONDS", 0)
    session = Session(
        control_state_values.PRIMARY_SESSION,
        control_state_values.PRIMARY_ACTOR,
        control_basic_support.source_name(source),
        str(tmp_path),
    )
    request = control_models.SendText(
        session_id=session.session_id,
        request_id=control_state_values.PRIMARY_REQUEST,
        text=fixture.TEST,
    )

    outcome = control_driver_support.controller(CODEX_HARNESS).execute(
        request,
        support_controls.control_context(
            session,
            FakeTerminal(screen_text=fixture.ASK_CODEX_TO_DO_ANYTHING_TEXT).plugin(),
        ),
    )

    assert isinstance(outcome, control_models.ControlResult)
    assert outcome.status == "indeterminate"
    assert outcome.reason == "Codex did not confirm the submitted message"


GOAL_OBJECTIVE = "Finish the goal sample"
GOAL_EVENT = (
    '{"timestamp":"2026-09-14T08:52:42.009Z","type":"event_msg","payload":{"type":"thread_goal_updated",'
    '"goal":{"objective":"Finish the goal sample","status":"active"}}}\n'
)


def test_codex_goal_command_confirms_from_goal_event(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Confirm a /goal send from the goal event, which has no user prompt echo."""
    source = tmp_path / fixture.ROLLOUT_JSONL_PATH
    source.write_text("", encoding=fixture.TEXT_ENCODING)
    submit_message = controller_send_state.submit_message

    def submit_and_record_goal(
        request: control_models.SendText,
        control_context: control_models.ControlContext,
        window_id: domain_ids.WindowId,
        send_state: controller_send_state.SendState,
    ) -> control_models.ControlResult:
        result = submit_message(request, control_context, window_id, send_state)
        with source.open("a", encoding=fixture.TEXT_ENCODING) as rollout:
            rollout.write(GOAL_EVENT)
        return result

    monkeypatch.setattr(controller_send_state, "submit_message", submit_and_record_goal)
    session = Session(
        control_state_values.PRIMARY_SESSION,
        control_state_values.PRIMARY_ACTOR,
        control_basic_support.source_name(source),
        str(tmp_path),
    )
    request = control_models.SendText(
        session_id=session.session_id,
        request_id=control_state_values.PRIMARY_REQUEST,
        text=f"/goal {GOAL_OBJECTIVE}",
    )

    outcome = control_driver_support.controller(CODEX_HARNESS).execute(
        request,
        support_controls.control_context(
            session,
            FakeTerminal(screen_text=fixture.ASK_CODEX_TO_DO_ANYTHING_TEXT).plugin(),
        ),
    )

    assert isinstance(outcome, control_models.MessageDeliveryResult)
    assert outcome.status == fixture.SENT
