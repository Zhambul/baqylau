# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness canonical translation tests from native fixture shapes."""

from pathlib import Path

import pytest

from domain import (
    attention as domain_attention,
    event_work,
    ids as domain_ids,
)
from harness.impl.claude_code.controls import (
    confirmdialog,
)
from harness.models import controls as control_models
from harness.models.session import (
    Session,
)
from tests.canonical_runtime import ProviderGraph
from tests.fake_terminal import FakeTerminal
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import (
    control_driver_support,
    control_state_values,
    control_submit_support,
    support_controls,
    support_values,
    vocabulary as fixture,
)


def test_codex_question_discussion_declines(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify codex question discussion declines then sends a new prompt."""
    calls = []
    monkeypatch.setattr(
        "harness.impl.codex.controls.controller.dialog.decline",
        lambda _terminal, _window, _prompts, message: calls.append(message),
    )
    session = Session(
        control_state_values.PRIMARY_SESSION,
        domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        "/work/rollout-session-one.jsonl",
        fixture.WORK_PATH,
    )
    attention = event_work.QuestionAsked(
        domain_ids.AttentionId(fixture.ATTENTION_ONE),
        (
            domain_attention.AttentionPrompt(
                prompt_id=domain_ids.QuestionId(fixture.QUESTION_ONE),
                title=None,
                prompt=fixture.CONTINUE,
                multiple=False,
                choices=(),
            ),
        ),
    )
    terminal = FakeTerminal()

    outcome = support_values.controller_of(
        ProviderGraph().registry.plugin(CODEX_HARNESS),
    ).execute(
        control_models.AnswerQuestion(
            session_id=session.session_id,
            request_id=control_state_values.PRIMARY_REQUEST,
            attention_id=domain_ids.AttentionId(fixture.ATTENTION_ONE),
            decision=control_models.AnswerDecision.DISCUSS,
            discussion=fixture.CHANGE_THE_APPROACH_TEXT,
        ),
        support_controls.control_context(session, terminal.plugin(), attention),
    )

    assert outcome.status == fixture.ACKNOWLEDGED
    assert calls == ["Continue in chat."]
    assert len(terminal.submitted) == 1
    assert terminal.submitted[0][1] == fixture.CHANGE_THE_APPROACH_TEXT


def test_claude_model_control_resolves_native(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify claude model control resolves the native confirmation."""
    transcript_path = tmp_path / fixture.SESSION_JSONL_PATH
    transcript_path.write_text(fixture.EMPTY_JSON_LINE)

    monkeypatch.setattr(
        "harness.impl.claude_code.controls.controller.tui.type_command",
        lambda terminal, window, text: control_submit_support.submit_model(transcript_path, terminal, window, text),
    )
    monkeypatch.setattr(
        "harness.impl.claude_code.controls.controller.confirmdialog.confirm",
        lambda _terminal, _window: confirmdialog.ConfirmOutcome(dialog=True, digit=fixture.ONE_TEXT),
    )
    application = ProviderGraph()
    session = Session(
        control_state_values.PRIMARY_SESSION,
        domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        str(transcript_path),
        fixture.WORK_PATH,
    )
    request = control_models.SelectModel(session.session_id, domain_ids.RequestId(fixture.REQUEST_ONE_ID), "opus")

    outcome = support_values.controller_of(application.registry.plugin(CLAUDE_CODE_HARNESS)).execute(
        request,
        support_controls.control_context(session, FakeTerminal().plugin()),
    )

    assert outcome.status == fixture.ACKNOWLEDGED
    assert isinstance(outcome, control_models.CommandResult)
    assert outcome.confirmation == "confirmed"


@pytest.mark.parametrize(
    (fixture.SCREEN, "keys"),
    [
        ("Change model?\n\u276f 1. Yes, switch\n  2. No, go back", [fixture.ENTER]),
        ("Change model?\n  1. Yes, switch\n\u276f 2. No, go back", [fixture.UP, fixture.ENTER]),
    ],
)
def test_claude_switch_confirmation_uses_cursor(
    screen: str,
    keys: list[str],
) -> None:
    """Verify claude switch confirmation uses cursor navigation."""
    driver = control_driver_support.CursorScreenDriver(
        screen,
        {
            (fixture.UP,): "Change model?\n\u276f 1. Yes, switch\n  2. No, go back",
            (fixture.ENTER,): "",
        },
    )

    outcome = confirmdialog.confirm(driver, control_state_values.PRIMARY_WINDOW, sleep=control_driver_support.no_sleep)

    assert outcome == confirmdialog.ConfirmOutcome(dialog=True, digit=fixture.ONE_TEXT)
    assert driver.keys == keys
