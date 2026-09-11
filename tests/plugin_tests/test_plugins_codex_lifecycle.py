# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex session lifecycle translation tests."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from repository.mapper import facts as mapper
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import raw_event

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_codex_session_start_hook_matches_rollout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify codex session start hook matches rollout metadata."""
    monkeypatch.setenv(fixture.CODEX_HOME_ENV, str(tmp_path))
    rollout_path = (
        tmp_path
        / fixture.SESSIONS
        / fixture.YEAR_TEXT
        / fixture.MONTH_TEXT
        / fixture.FOURTEEN_TEXT
        / "rollout-2026-08-14T12-00-00-session-one.jsonl"
    )
    rollout_path.parent.mkdir(parents=True)
    rollout_path.write_text(
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.SESSION_META_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.ID_FIELD: fixture.SESSION_ONE_ID,
                    fixture.CWD_FIELD: fixture.WORK_PATH,
                    fixture.THREAD_SOURCE: fixture.USER,
                    "forked_from_id": fixture.SESSION_BEFORE_REWIND_ID,
                    "forked_from_ordinal_exclusive": 15,
                },
            },
        )
        + "\n",
    )
    translator = CodexCanonicalTranslator()
    hook = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.SESSION_START_HOOK,
                fixture.CWD_FIELD: fixture.WORK_PATH,
                fixture.TRANSCRIPT_PATH: str(rollout_path),
            },
            harness=CODEX_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="session-hook",
        ),
    )
    rollout = translator.translate(
        replace(
            raw_event(
                {
                    fixture.TIMESTAMP_FIELD: "2026-08-14T12:00:00Z",
                    fixture.TYPE_FIELD: fixture.SESSION_META_ID,
                    fixture.PAYLOAD_FIELD: {
                        fixture.CWD_FIELD: fixture.WORK_PATH,
                        fixture.ORIGINATOR: fixture.CODEX_TUI,
                        "forked_from_id": fixture.SESSION_BEFORE_REWIND_ID,
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="session-rollout",
                source_position=fixture.ZERO_TEXT,
            ),
            source_name=str(rollout_path),
        ),
    )

    assert hook.decision == fixture.TRANSLATED
    # the rollout record carries its own timestamp; the identities and payloads converge
    assert [event.event_id for event in hook.canonical_events] == [event.event_id for event in rollout.canonical_events]
    assert [mapper.payload_json(event) for event in hook.canonical_events] == [
        mapper.payload_json(event) for event in rollout.canonical_events
    ]
