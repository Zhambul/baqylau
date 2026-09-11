# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness canonical translation tests from native fixture shapes."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from harness.impl.codex.plugin import build_plugin
from harness.models import controls as control_models
from harness.models.session import (
    Session,
)
from harness.runtime import (
    HarnessRuntimeConfig,
)
from tests.fake_terminal import FakeTerminal
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import (
    control_basic_support,
    control_codex_submit_support,
    control_driver_support,
    control_state_values,
    control_submit_support,
    support_controls,
    support_values,
    vocabulary as fixture,
)


def test_codex_active_send_uses_harness_window(tmp_path: Path) -> None:
    """Verify codex active send uses the harness window."""
    session = Session(
        control_state_values.PRIMARY_SESSION,
        control_state_values.PRIMARY_ACTOR,
        str(tmp_path / fixture.ROLLOUT_JSONL_PATH),
        control_codex_submit_support.temporary_directory_name(tmp_path),
        harness_process_id=fixture.CODEX_ACTIVE_PROCESS_ID,
    )
    request = control_models.SendText(
        session_id=session.session_id,
        request_id=control_state_values.PRIMARY_REQUEST,
        text="queued Codex prompt",
    )

    plugin = build_plugin(
        HarnessRuntimeConfig(fixture.CODEX_HARNESS, tmp_path / "configured-codex-home"),
    )
    assert plugin.controller is not None
    terminal = FakeTerminal()
    outcome = support_values.controller_of(plugin).execute(
        request,
        support_controls.control_context(session, terminal.plugin(), lead_active=True),
    )

    assert isinstance(outcome, control_models.MessageDeliveryResult)
    assert outcome.status == fixture.QUEUED
    assert terminal.submitted[-1][1] == "queued Codex prompt"


def test_codex_idle_send_waits_for_native_prompt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify codex idle send waits for the native prompt."""
    source = tmp_path / fixture.ROLLOUT_JSONL_PATH
    control_basic_support.clear_event_source(source)
    terminal = FakeTerminal(screen_text=fixture.ASK_CODEX_TO_DO_ANYTHING_TEXT)
    native_submit = terminal.submit_text

    monkeypatch.setattr(
        terminal,
        "submit_text",
        lambda request: control_submit_support.submit_codex_prompt(native_submit, source, request),
    )
    session = Session(
        control_state_values.PRIMARY_SESSION,
        control_state_values.PRIMARY_ACTOR,
        control_basic_support.source_name(source),
        control_codex_submit_support.temporary_directory_name(tmp_path),
    )
    outcome = control_driver_support.controller(CODEX_HARNESS).execute(
        control_models.SendText(
            session_id=session.session_id,
            request_id=control_state_values.PRIMARY_REQUEST,
            text=fixture.TEST,
        ),
        support_controls.control_context(session, terminal.plugin()),
    )

    assert isinstance(outcome, control_models.MessageDeliveryResult)
    assert outcome.status == fixture.SENT


def test_codex_plan_command_waits_for_plan_mode(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify codex plan command waits for plan mode."""
    source = tmp_path / fixture.ROLLOUT_JSONL_PATH
    source.write_text("", encoding=fixture.TEXT_ENCODING)
    terminal = FakeTerminal(screen_text=fixture.ASK_CODEX_TO_DO_ANYTHING_TEXT)
    native_submit = terminal.submit_text

    monkeypatch.setattr(
        terminal,
        "submit_text",
        lambda request: control_codex_submit_support.submit_codex_plan(native_submit, terminal, request),
    )
    session = Session(
        control_state_values.PRIMARY_SESSION,
        control_state_values.PRIMARY_ACTOR,
        control_basic_support.source_name(source),
        str(tmp_path),
    )
    outcome = control_driver_support.controller(CODEX_HARNESS).execute(
        control_models.SendText(
            session_id=session.session_id,
            request_id=control_state_values.PRIMARY_REQUEST,
            text="/plan",
        ),
        support_controls.control_context(session, terminal.plugin()),
    )

    assert isinstance(outcome, control_models.MessageDeliveryResult)
    assert outcome.status == fixture.SENT


def _patch_codex_rename_submit(
    monkeypatch: pytest.MonkeyPatch,
    terminal: FakeTerminal,
    rename_state: SimpleNamespace,
) -> None:
    native_submit = terminal.submit_text
    monkeypatch.setattr(
        terminal,
        "submit_text",
        lambda request: control_codex_submit_support.submit_codex_rename(native_submit, rename_state, request),
    )


def test_codex_rename_command_waits_for_native(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify codex rename command waits for the native title."""
    source = tmp_path / fixture.ROLLOUT_JSONL_PATH
    source.write_text("", encoding=fixture.TEXT_ENCODING)
    terminal = FakeTerminal(screen_text=fixture.ASK_CODEX_TO_DO_ANYTHING_TEXT)
    rename_state = SimpleNamespace(completed=False)

    _patch_codex_rename_submit(monkeypatch, terminal, rename_state)
    monkeypatch.setattr(
        "harness.impl.codex.canonical.title.CodexThreadTitleRepository.read_title",
        lambda _repository, _source: SimpleNamespace(text="Stable name") if rename_state.completed else None,
    )
    session = Session(
        control_state_values.PRIMARY_SESSION,
        control_state_values.PRIMARY_ACTOR,
        control_basic_support.source_name(source),
        str(tmp_path),
    )
    outcome = support_values.controller_of(
        build_plugin(
            HarnessRuntimeConfig(
                fixture.CODEX_HARNESS,
                tmp_path / fixture.CODEX_HOME_ID,
            ),
        ),
    ).execute(
        control_models.SendText(
            session.session_id,
            control_state_values.PRIMARY_REQUEST,
            text="/rename Stable name",
        ),
        support_controls.control_context(session, terminal.plugin()),
    )

    assert isinstance(outcome, control_models.MessageDeliveryResult)
    assert outcome.status == fixture.SENT
