# Copyright (c) 2026 Zhambyl Yermagambet
"""Common support for source tests."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from domain import (
    ids as domain_ids,
)
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import raw_event

if TYPE_CHECKING:
    from pathlib import Path

    from harness.models import raw_events as raw_event_models

PRIMARY_LEAD_ACTOR = domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID)


def session_start_hook_event(session_id: str, source_path: Path) -> raw_event_models.RawEvent:
    """Build a session-start hook for a Codex source file.

    Returns:
        The raw hook event with the supplied session identity and source path.

    """
    return replace(
        raw_event(
            {
                fixture.SESSION_ID_FIELD: session_id,
                fixture.TRANSCRIPT_PATH: source_path.as_posix(),
                fixture.CWD_FIELD: fixture.WORK_PATH,
                fixture.HOOK_EVENT_NAME_FIELD: fixture.SESSION_START_HOOK,
            },
            harness=CODEX_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id=f"hook-{session_id}",
        ),
        session_id=domain_ids.SessionId(session_id),
    )


def codex_hook_event(
    source_path: Path,
    hook_name: str,
    raw_event_id: str,
) -> raw_event_models.RawEvent:
    """Build a Codex hook with fixed terminal and process identities.

    Returns:
        The raw event for the supplied hook name and source path.

    """
    return replace(
        raw_event(
            {
                fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
                fixture.TRANSCRIPT_PATH: source_path.as_posix(),
                fixture.CWD_FIELD: fixture.WORK_PATH,
                fixture.HOOK_EVENT_NAME_FIELD: hook_name,
                fixture.HOOK_EVENT_ID_FIELD: raw_event_id,
            },
            harness=CODEX_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id=raw_event_id,
        ),
        terminal_window_id=domain_ids.WindowId(fixture.WINDOW_ONE_ID),
        harness_process_id=fixture.FIXTURE_PROCESS_ID,
    )
