# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness canonical translation tests from native fixture shapes."""

import json
from pathlib import Path

import pytest

from domain import (
    attention as domain_attention,
    event_work,
    ids as domain_ids,
)
from harness.impl.claude_code.controls import (
    controller as claudecontroller,
    controller_values,
    tui as claude_tui,
)
from harness.models import controls as control_models
from harness.models.session import (
    Session,
)
from tests.canonical_runtime import ProviderGraph
from tests.fake_terminal import FakeTerminal
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import (
    control_basic_support,
    control_driver_support,
    control_state_values,
    control_submit_support,
    support_controls,
    support_values,
    vocabulary as fixture,
)

TRANSCRIPT_PREFIX = "prefix\n"


def test_claude_active_send_is_not_called_queued(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify claude active send is not called queued without native confirmation."""
    source = control_basic_support.session_event_source(tmp_path)
    source.write_text("", encoding=fixture.TEXT_ENCODING)
    submit_requirements: list[bool] = []

    monkeypatch.setattr(
        claude_tui,
        "type_command",
        lambda driver, window, text, *, ensure_submit=False: control_submit_support.accept_without_confirmation(
            submit_requirements,
            driver,
            window,
            text,
            ensure_submit=ensure_submit,
        ),
    )
    monkeypatch.setattr(controller_values, "NATIVE_TEXT_CONFIRM_TIMEOUT_SECONDS", 0)
    session = Session(
        control_state_values.PRIMARY_SESSION,
        control_state_values.PRIMARY_ACTOR,
        control_basic_support.source_name(source),
        str(tmp_path),
    )
    request = control_models.SendText(
        session_id=session.session_id,
        request_id=control_state_values.PRIMARY_REQUEST,
        text="unconfirmed prompt",
    )

    outcome = control_driver_support.controller(CLAUDE_CODE_HARNESS).execute(
        request,
        support_controls.control_context(
            session,
            FakeTerminal(screen_text=support_controls.claude_composer_screen()).plugin(),
            lead_active=True,
        ),
    )

    assert outcome.status == fixture.REJECTED
    assert isinstance(outcome, control_models.ControlResult)
    assert outcome.reason == "Claude Code did not confirm the message"
    assert submit_requirements == [False, False]


def test_claude_native_queue_state_changes(tmp_path: Path) -> None:
    """Verify claude native queue state changes to sent when the queue drains."""
    source = tmp_path / fixture.SESSION_JSONL_PATH
    source.write_text(TRANSCRIPT_PREFIX, encoding=fixture.TEXT_ENCODING)
    position = source.stat().st_size
    enqueue = {
        fixture.TYPE_FIELD: fixture.QUEUE_OPERATION_ID,
        fixture.OPERATION_FIELD: fixture.ENQUEUE,
        fixture.TIMESTAMP_FIELD: "2026-08-25T00:00:00.000Z",
        "sessionId": fixture.SESSION_ONE_ID,
        fixture.CONTENT_FIELD: fixture.NATIVE_PROMPT_TEXT,
    }
    with source.open(fixture.LETTER_A, encoding=fixture.TEXT_ENCODING) as transcript_file:
        transcript_file.write(f"{json.dumps(enqueue)}\n")

    assert (
        claudecontroller.native_text_state(
            str(source),
            position,
            fixture.NATIVE_PROMPT_TEXT,
        )
        == claudecontroller.NATIVE_TEXT_QUEUED
    )

    with source.open(fixture.LETTER_A, encoding=fixture.TEXT_ENCODING) as transcript_file:
        transcript_file.write(f"{json.dumps({**enqueue, fixture.OPERATION_FIELD: 'remove'})}\n")

    assert (
        claudecontroller.native_text_state(
            str(source),
            position,
            fixture.NATIVE_PROMPT_TEXT,
        )
        == claudecontroller.NATIVE_TEXT_SENT
    )


def test_claude_native_prompt_confirms_text(tmp_path: Path) -> None:
    """Verify claude native prompt confirms text delivery."""
    source = tmp_path / fixture.SESSION_JSONL_PATH
    source.write_text(TRANSCRIPT_PREFIX, encoding=fixture.TEXT_ENCODING)
    position = source.stat().st_size
    with source.open(fixture.LETTER_A, encoding=fixture.TEXT_ENCODING) as transcript_file:
        transcript_file.write(
            json.dumps(
                {
                    fixture.TYPE_FIELD: fixture.USER,
                    fixture.UUID_FIELD: "native-prompt-one",
                    fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: fixture.NATIVE_PROMPT_TEXT},
                },
            )
            + "\n",
        )

    assert (
        claudecontroller.native_text_state(
            str(source),
            position,
            fixture.NATIVE_PROMPT_TEXT,
        )
        == claudecontroller.NATIVE_TEXT_SENT
    )


def test_claude_native_slash_command_confirms(tmp_path: Path) -> None:
    """Verify claude native slash command confirms text delivery."""
    source = tmp_path / fixture.SESSION_JSONL_PATH
    source.write_text(TRANSCRIPT_PREFIX, encoding=fixture.TEXT_ENCODING)
    position = source.stat().st_size
    with source.open(fixture.LETTER_A, encoding=fixture.TEXT_ENCODING) as transcript_file:
        transcript_file.write(
            json.dumps(
                {
                    fixture.TYPE_FIELD: fixture.SYSTEM,
                    fixture.SUBTYPE: "local_command",
                    fixture.CONTENT_FIELD: (
                        "<command-name>/rename</command-name>"
                        "<command-message>rename</command-message>"
                        "<command-args>Native E2E 738</command-args>"
                    ),
                },
            )
            + "\n",
        )

    assert (
        claudecontroller.native_text_state(
            str(source),
            position,
            "/rename Native E2E 738",
        )
        == claudecontroller.NATIVE_TEXT_SENT
    )


def test_claude_question_discussion_is_delivered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify claude question discussion is delivered after declining."""
    calls = []
    source = tmp_path / fixture.SESSION_JSONL_PATH
    source.write_text("", encoding=fixture.TEXT_ENCODING)
    monkeypatch.setattr(
        "harness.impl.claude_code.controls.controller.askdialog.drive",
        lambda _terminal, _window, ask_request: calls.append(("dialog", ask_request.chat)),
    )

    monkeypatch.setattr(
        "harness.impl.claude_code.controls.controller.tui.type_command",
        lambda terminal, window, text, *, ensure_submit=False: control_submit_support.submit_discussion(
            (source, calls),
            terminal,
            window,
            text,
            ensure_submit=ensure_submit,
        ),
    )
    session = Session(
        control_state_values.PRIMARY_SESSION,
        control_state_values.PRIMARY_ACTOR,
        str(source),
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

    outcome = support_values.controller_of(
        ProviderGraph().registry.plugin(CLAUDE_CODE_HARNESS),
    ).execute(
        control_models.AnswerQuestion(
            session_id=session.session_id,
            request_id=control_state_values.PRIMARY_REQUEST,
            attention_id=domain_ids.AttentionId(fixture.ATTENTION_ONE),
            decision=control_models.AnswerDecision.DISCUSS,
            discussion=fixture.CHANGE_THE_APPROACH_TEXT,
        ),
        support_controls.control_context(session, FakeTerminal().plugin(), attention),
    )

    control_basic_support.assert_acknowledged(outcome)
    assert calls == [
        ("dialog", True),
        ("ensure-submit", False),
        ("discussion", fixture.CHANGE_THE_APPROACH_TEXT),
    ]


def test_claude_attachment_delivery_keeps_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify claude attachment delivery keeps the prompt visible for verification."""
    delivered: list[tuple[str, bool]] = []
    source = tmp_path / fixture.SESSION_JSONL_PATH
    source.write_text("", encoding=fixture.TEXT_ENCODING)

    monkeypatch.setattr(
        "harness.impl.claude_code.controls.controller.tui.type_command",
        lambda terminal, window, text, *, ensure_submit=False: control_submit_support.submit_attachment(
            (source, delivered),
            terminal,
            window,
            text,
            ensure_submit=ensure_submit,
        ),
    )
    session = Session(
        control_state_values.PRIMARY_SESSION,
        control_state_values.PRIMARY_ACTOR,
        str(source),
        fixture.WORK_PATH,
    )
    request = control_models.SendText(
        session_id=session.session_id,
        request_id=control_state_values.PRIMARY_REQUEST,
        text="Inspect the attached image.",
        attachments=(
            control_models.AttachmentReference(
                "/work/marker.png",
                "marker.png",
                fixture.PNG_MEDIA_TYPE,
            ),
        ),
    )

    outcome = support_values.controller_of(
        ProviderGraph().registry.plugin(CLAUDE_CODE_HARNESS),
    ).execute(
        request,
        support_controls.control_context(
            session,
            FakeTerminal(
                screen_text=support_controls.claude_composer_screen(),
            ).plugin(),
        ),
    )

    assert outcome.status == fixture.SENT
    assert delivered == [
        (
            'Image attachment "marker.png": /work/marker.png\nInspect the attached image.',
            True,
        ),
    ]


def test_claude_pasted_queue_prompt_is_queued(tmp_path: Path) -> None:
    """A queued prompt inside Claude Code's paste tag is the sent prompt, so it is not typed again."""
    source = tmp_path / fixture.SESSION_JSONL_PATH
    source.write_text(TRANSCRIPT_PREFIX, encoding=fixture.TEXT_ENCODING)
    position = source.stat().st_size
    enqueue = {
        fixture.TYPE_FIELD: fixture.QUEUE_OPERATION_ID,
        fixture.OPERATION_FIELD: fixture.ENQUEUE,
        fixture.TIMESTAMP_FIELD: "2026-09-25T00:00:00.000Z",
        "sessionId": fixture.SESSION_ONE_ID,
        fixture.CONTENT_FIELD: f'<pasted_content id="5122">\n{fixture.NATIVE_PROMPT_TEXT}\n</pasted_content id="5122">',
    }
    with source.open(fixture.LETTER_A, encoding=fixture.TEXT_ENCODING) as transcript_file:
        transcript_file.write(f"{json.dumps(enqueue)}\n")

    state = claudecontroller.native_text_state(str(source), position, fixture.NATIVE_PROMPT_TEXT)
    assert state == claudecontroller.NATIVE_TEXT_QUEUED
