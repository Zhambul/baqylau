# Copyright (c) 2026 Zhambyl Yermagambet
"""Test control effect launch."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from domain import (
    event_actor,
    event_session,
    ids as domain_ids,
    messaging,
)
from engine.interpret import translators as interpret_translators
from harness.models.session import (
    Session,
)
from harness.services.launch_effects import SessionLaunchEffectRecorder
from tests import (
    control_effect_native as native,
    control_effect_stores as stores,
    control_effect_values as control_values,
)
from tests.harness_names import CODEX_HARNESS

if TYPE_CHECKING:
    from repository.contract.facts import RawEventRepository
    from repository.contract.sessions import SessionRepository

RESUME_EVENT_COUNT = 2


def test_confirmed_resume_launch_reopens_exact() -> None:
    """Verify a confirmed resume launch reopens the exact session and lead."""
    raw_events = stores.RawEvents()
    session = Session(
        control_values.TEST_SESSION_ID,
        control_values.TEST_ACTOR_ID,
        "/rollouts/session-one.jsonl",
        control_values.TEST_WORKING_DIRECTORY,
    )
    recorder = SessionLaunchEffectRecorder(
        cast("RawEventRepository", raw_events),
        cast("SessionRepository", stores.Sessions(session)),
    )

    recorder.resumed(
        CODEX_HARNESS,
        session.session_id,
        domain_ids.WindowId("window-two"),
    )

    assert len(raw_events.events) == 1
    raw_event = raw_events.events[0]
    assert (
        raw_event.session_id,
        raw_event.actor_id,
        raw_event.terminal_window_id,
        raw_event.harness_process_id,
    ) == (session.session_id, session.lead_actor_id, "window-two", None)
    translated = interpret_translators.SessionResumeTranslator().translate(raw_event)
    assert len(translated.canonical_events) == RESUME_EVENT_COUNT
    assert (translated.canonical_events[0].payload, translated.canonical_events[1].payload) == (
        event_session.SessionStarted(
            control_values.TEST_WORKING_DIRECTORY,
            "/rollouts/session-one.jsonl",
            session.session_id,
            None,
            None,
            None,
            None,
        ),
        event_actor.ActorStarted("lead", messaging.ActorRole.LEAD),
    )


def test_native_start_hook_and_resume_observation() -> None:
    """Verify a native start hook and resume observation share one run identity."""
    sequence = native.native_start_sequence()

    assert [event.event_id for event in sequence.native.canonical_events] == [
        event.event_id for event in sequence.resumed.canonical_events
    ]

    assert [event.event_id for event in sequence.another_run.canonical_events] != [
        event.event_id for event in sequence.native.canonical_events
    ]

    assert sequence.native_end.canonical_events[0].event_id == sequence.liveness_end.canonical_events[0].event_id
