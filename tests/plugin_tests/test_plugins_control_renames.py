# Copyright (c) 2026 Zhambyl Yermagambet
"""Cross-harness canonical translation tests from native fixture shapes."""

from pathlib import Path

import pytest

from domain import (
    ids as domain_ids,
    work_state,
)
from harness.impl.codex.canonical import title as codex_title
from harness.models import controls as control_models
from harness.models.session import (
    Session,
)
from tests.canonical_runtime import ProviderGraph
from tests.fake_terminal import FakeTerminal
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import (
    control_draft_support,
    control_driver_support,
    control_state_values,
    control_submit_support,
    support_controls,
    support_values,
    vocabulary as fixture,
)


@pytest.mark.parametrize(
    "harness",
    [
        fixture.CLAUDE_CODE_HARNESS,
        fixture.CODEX_HARNESS,
    ],
)
def test_parked_rename_uses_only_owning_harness(
    monkeypatch: pytest.MonkeyPatch,
    harness: domain_ids.HarnessName,
) -> None:
    """Verify parked rename uses only the owning harness title store."""
    calls: list[tuple[str, str]] = []
    if harness == fixture.CLAUDE_CODE_HARNESS:
        monkeypatch.setattr(
            "harness.impl.claude_code.canonical.transcript_titles.titles",
            support_controls.RecordingTitles(calls),
        )
    else:
        monkeypatch.setattr(
            "harness.impl.codex.canonical.title.CodexThreadTitleRepository.renameable",
            lambda _self, _source_reference: True,
        )
        monkeypatch.setattr(
            "harness.impl.codex.canonical.title.CodexThreadTitleRepository.set_title",
            lambda repository, source_reference, new_title: control_submit_support.record_codex_title(
                calls,
                repository,
                source_reference,
                new_title,
            ),
        )
    application = ProviderGraph()
    session = Session(
        control_state_values.PRIMARY_SESSION,
        domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        "/work/native-session",
        fixture.WORK_PATH,
    )
    request = control_models.RenameSession(
        session.session_id, domain_ids.RequestId(fixture.REQUEST_ONE_ID), fixture.NEW_TITLE_TEXT,
    )

    outcome = support_values.controller_of(application.registry.plugin(harness)).execute(
        request,
        support_controls.control_context(session, FakeTerminal().plugin(), window_id=None),
    )

    assert isinstance(outcome, control_models.DurableTitleResult)
    assert outcome.status == fixture.ACKNOWLEDGED
    assert calls == [(session.source_reference, fixture.NEW_TITLE_TEXT)]


def test_live_codex_rename_waits_for_native_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify live codex rename waits for the native title store."""
    monkeypatch.setattr(
        "harness.impl.codex.canonical.title.CodexThreadTitleRepository.read_title",
        lambda _self, _source_reference: codex_title.CodexNativeTitle(
            fixture.NEW_TITLE_TEXT,
            work_state.TitleOrigin.AUTOMATIC,
        ),
    )
    monkeypatch.setattr(
        "harness.impl.codex.canonical.title.CodexThreadTitleRepository.set_title",
        lambda *_args: pytest.fail("a live rename must not write the Codex store"),
    )
    application = ProviderGraph()
    session = Session(
        domain_ids.SessionId(fixture.SESSION_ONE_ID),
        domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        "/work/rollout-session-one.jsonl",
        fixture.WORK_PATH,
    )
    terminal = FakeTerminal(screen_text=fixture.ASK_CODEX_TO_DO_ANYTHING_TEXT)

    outcome = support_values.controller_of(application.registry.plugin(CODEX_HARNESS)).execute(
        control_models.RenameSession(
            session.session_id, domain_ids.RequestId(fixture.REQUEST_ONE_ID), fixture.NEW_TITLE_TEXT,
        ),
        support_controls.control_context(session, terminal.plugin()),
    )

    assert outcome.status == fixture.ACKNOWLEDGED
    assert terminal.submitted[-1][1] == "/rename New title"
    assert not terminal.renamed_tabs


def test_live_claude_rename_restores_visual_mode(tmp_path: Path) -> None:
    """Verify live claude rename restores a visual mode draft."""
    source = tmp_path / fixture.SESSION_ONE_JSONL_PATH
    source.write_text("", encoding=fixture.TEXT_ENCODING)
    terminal = control_draft_support.ClaudeDraftTerminal(source)
    session = Session(
        domain_ids.SessionId(fixture.SESSION_ONE_ID),
        domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        str(source),
        str(tmp_path),
    )

    outcome = control_driver_support.controller(CLAUDE_CODE_HARNESS).execute(
        control_models.RenameSession(
            session.session_id, domain_ids.RequestId(fixture.REQUEST_ONE_ID), fixture.NEW_TITLE_TEXT,
        ),
        support_controls.control_context(session, terminal.plugin()),
    )

    assert outcome.status == fixture.ACKNOWLEDGED
    assert terminal.text == fixture.TEST
    assert terminal.submitted[-1][1] == "/rename New title"
    assert terminal.keys[0][1] == fixture.ESCAPE
    assert terminal.keys[1][1] == "i"


def test_live_codex_rename_restores_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify live codex rename restores an existing draft."""
    terminal = control_draft_support.CodexDraftTerminal()
    monkeypatch.setattr(
        "harness.impl.codex.canonical.title.CodexThreadTitleRepository.read_title",
        lambda _self, _source_reference: (
            codex_title.CodexNativeTitle(fixture.NEW_TITLE_TEXT, work_state.TitleOrigin.AUTOMATIC)
            if terminal.submitted
            else None
        ),
    )
    session = Session(
        domain_ids.SessionId(fixture.SESSION_ONE_ID),
        domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        "/work/rollout-session-one.jsonl",
        fixture.WORK_PATH,
    )

    outcome = control_driver_support.controller(CODEX_HARNESS).execute(
        control_models.RenameSession(
            session.session_id, domain_ids.RequestId(fixture.REQUEST_ONE_ID), fixture.NEW_TITLE_TEXT,
        ),
        support_controls.control_context(session, terminal.plugin()),
    )

    assert outcome.status == fixture.ACKNOWLEDGED
    assert terminal.text == fixture.TEST
    assert terminal.submitted[-1][1] == "/rename New title"
    assert fixture.ESCAPE not in [key for _window, key in terminal.keys]
