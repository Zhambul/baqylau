# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness canonical translation tests from native fixture shapes."""

from pathlib import Path

import pytest

from domain import (
    event_shell,
    ids as domain_ids,
    outcomes,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.claude_code.controls import (
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
    control_codex_submit_support,
    control_driver_support,
    control_state_values,
    control_submit_support,
)

# Keep control drivers separate from shared fixture helpers.
# isort: split

from tests.plugin_tests import (
    support_controls,
    support_events,
    support_values,
    vocabulary as fixture,
)


def test_claude_composer_owns_input_box_grammar() -> None:
    """Verify claude composer owns input box grammar."""
    divider = fixture.GREY_ANSI_SEQUENCE + fixture.DIVIDER_CHARACTER * fixture.DIVIDER_WIDTH
    screen = f"{divider}\n\x1b[m\u276f\xa0\x1b[22;2mapply the fix\n{divider}"

    terminal = FakeTerminal(screen_text=screen)

    plugin = ProviderGraph().registry.plugin(CLAUDE_CODE_HARNESS)
    state = control_driver_support.read_composer_state(plugin, terminal)
    assert state is not None

    assert state.suggestion == "apply the fix"
    assert not state.typed_text


def test_claude_empty_vim_composer_is_readable() -> None:
    """Verify Claude's promptless empty Vim editor is readable."""
    divider = fixture.DIVIDER_CHARACTER * fixture.DIVIDER_WIDTH
    screen = f"{divider}\n✻ Worked for 4s · done\n{divider}\n-- INSERT --"
    terminal = FakeTerminal(screen_text=screen)

    plugin = ProviderGraph().registry.plugin(CLAUDE_CODE_HARNESS)
    state = control_driver_support.read_composer_state(plugin, terminal)

    assert state is not None
    assert not state.typed_text


def test_claude_interrupt_control_waits(tmp_path: Path) -> None:
    """Verify claude interrupt control waits for the native abort marker."""
    transcript_path = control_basic_support.session_event_source(tmp_path)
    transcript_path.write_text(fixture.EMPTY_JSON_LINE)

    session = Session(
        control_state_values.PRIMARY_SESSION,
        control_state_values.PRIMARY_ACTOR,
        str(transcript_path),
        fixture.WORK_PATH,
    )

    outcome = support_values.controller_of(
        ProviderGraph().registry.plugin(CLAUDE_CODE_HARNESS),
    ).execute(
        control_models.Interrupt(session.session_id, control_state_values.PRIMARY_REQUEST),
        support_controls.control_context(
            session, control_driver_support.InterruptingTerminal(transcript_path).plugin(),
        ),
    )

    control_basic_support.assert_acknowledged(outcome)
    assert isinstance(outcome, control_models.InterruptResult)
    assert outcome.corroborated is True


def test_claude_signal_killed_shell_is_cancelled() -> None:
    """Verify claude a signal killed shell is cancelled."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        support_events.raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: "shell-start",
                fixture.MESSAGE_FIELD: {
                    fixture.ID_FIELD: fixture.MESSAGE_ONE_ID,
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                            fixture.ID_FIELD: fixture.SHELL_ONE_ID,
                            fixture.NAME_FIELD: fixture.BASH_TOOL,
                            fixture.INPUT_FIELD: {fixture.COMMAND_FIELD: "sleep 30"},
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="shell-start",
        ),
    )

    finished = translator.translate(
        support_events.raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: fixture.SHELL_RESULT_ID,
                fixture.MESSAGE_FIELD: {
                    fixture.ROLE_FIELD: fixture.USER,
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                            fixture.TOOL_USE_ID_FIELD: fixture.SHELL_ONE_ID,
                            fixture.IS_ERROR: True,
                            fixture.CONTENT_FIELD: "Exit code 137",
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id=fixture.SHELL_RESULT_ID,
        ),
    )

    shell = support_events.payloads(finished, event_shell.ShellFinished)[0].payload
    assert shell.exit_code == fixture.INTERRUPTED_EXIT_CODE
    assert shell.outcome == outcomes.Outcome.CANCELLED


@pytest.mark.parametrize(
    ("harness", "interrupt_handler"),
    [
        (fixture.CODEX_HARNESS, "harness.impl.codex.controls.controller.InterruptHandler.__call__"),
        (
            fixture.CLAUDE_CODE_HARNESS,
            "harness.impl.claude_code.controls.controller.InterruptHandler.__call__",
        ),
    ],
)
def test_closing_active_session_confirms(
    monkeypatch: pytest.MonkeyPatch,
    harness: domain_ids.HarnessName,
    interrupt_handler: str,
) -> None:
    """Verify closing an active session confirms the interrupt before it closes the tab."""
    calls: list[control_models.Interrupt] = []

    monkeypatch.setattr(
        interrupt_handler,
        lambda interruptcontroller, request, context: control_basic_support.acknowledge_interrupt(
            calls,
            interruptcontroller,
            request,
            context,
        ),
    )
    session = Session(
        control_state_values.PRIMARY_SESSION,
        control_state_values.PRIMARY_ACTOR,
        fixture.WORK_SESSION_JSONL_PATH,
        fixture.WORK_PATH,
    )
    terminal = FakeTerminal()
    request = control_models.CloseSession(session.session_id, domain_ids.RequestId("close-one"))

    outcome = support_values.controller_of(ProviderGraph().registry.plugin(harness)).execute(
        request,
        support_controls.control_context(session, terminal.plugin(), lead_active=True),
    )

    control_basic_support.assert_acknowledged(outcome)
    assert calls == [control_models.Interrupt(session.session_id, request.request_id)]
    assert terminal.closed_tabs == [fixture.WINDOW_ONE_ID]


def test_claude_active_send_retries_until_native(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify claude active send retries until the native queue accepts it."""
    source = control_basic_support.session_event_source(tmp_path)
    control_basic_support.clear_event_source(source)
    delivered: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        claude_tui,
        "type_command",
        lambda driver, window, text, *, ensure_submit=False: control_submit_support.submit_claude_queue(
            (source, delivered),
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
        control_codex_submit_support.temporary_directory_name(tmp_path),
    )
    request = control_models.SendText(
        session_id=session.session_id,
        request_id=control_state_values.PRIMARY_REQUEST,
        text="queued native prompt",
    )

    outcome = control_driver_support.controller(CLAUDE_CODE_HARNESS).execute(
        request,
        support_controls.control_context(
            session,
            FakeTerminal(screen_text=support_controls.claude_composer_screen()).plugin(),
            lead_active=True,
        ),
    )

    assert isinstance(outcome, control_models.MessageDeliveryResult)
    assert outcome.status == fixture.QUEUED
    assert delivered == [
        ("queued native prompt", False),
        ("queued native prompt", False),
    ]
