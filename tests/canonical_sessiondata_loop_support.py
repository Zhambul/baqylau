# Copyright (c) 2026 Zhambyl Yermagambet
"""Canonical sessiondata loop support."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from repository.impl.sqlite.interpretations import SqliteInterpretationRepository
from tests import (
    canonical_sessiondata_components as sessiondata_components,
    canonical_sessiondata_fixtures as session_fixtures,
    canonical_sessiondata_folding as folding,
    canonical_sessiondata_loop_models as loop_models,
    canonical_sessiondata_values as session_values,
)
from tests.canonical_sessiondata_components import domain as session_domain
from tests.harness_names import CODEX_HARNESS

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


def loop_over(
    tmp_path: Path,
    payloads: Sequence[session_domain.event_base.EventPayload],
    *,
    reaction: sessiondata_components.harness.contract.CanonicalEventReaction | None = None,
    listener: sessiondata_components.engine.contract.AppliedActorListener | None = None,
) -> tuple[
    sessiondata_components.engine.loop.ReactionLoop,
    sessiondata_components.repository.session_data.SqliteSessionDataRepository,
    loop_models.RecordingAudit,
]:
    """Build a reaction loop with facts stored in a test database.

    Returns:
        The loop, its session-data repository, and its audit recorder.

    """
    database = sessiondata_components.repository.databases.main_database(str(tmp_path / "main.db"))
    events = sessiondata_components.repository.canonical_events.SqliteCanonicalEventRepository(database)
    read_model = sessiondata_components.repository.session_data.SqliteSessionDataRepository(database)
    audit = loop_models.RecordingAudit()
    loop = sessiondata_components.engine.loop.ReactionLoop(
        sessiondata_components.engine.loop.ReactionLoopDependencies(
            canonical_fact_reader=SqliteInterpretationRepository(database),
            session_data_repository=read_model,
            reactions=() if reaction is None else (reaction,),
            session_entry_writer=sessiondata_components.engine.entries.EntryWriter(),
            writers=session_values.WRITERS,
            listeners=() if listener is None else (listener,),
            harness_registry=loop_models.NoReactors(),
            harness_reactor_context=None,
            audit_recorder=audit,
        ),
    )
    record_events(database, events, payloads)
    return loop, read_model, audit


def loop_and_read_model(
    tmp_path: Path,
    payloads: Sequence[session_domain.event_base.EventPayload],
    *,
    reaction: sessiondata_components.harness.contract.CanonicalEventReaction | None = None,
    listener: sessiondata_components.engine.contract.AppliedActorListener | None = None,
) -> tuple[
    sessiondata_components.engine.loop.ReactionLoop,
    sessiondata_components.repository.session_data.SqliteSessionDataRepository,
]:
    """Build a loop and its read model for the supplied facts.

    Returns:
        The reaction loop and its session-data repository.

    """
    loop, read_model, _audit = loop_over(
        tmp_path,
        payloads,
        reaction=reaction,
        listener=listener,
    )
    return loop, read_model


def failure_locations(audit: loop_models.RecordingAudit) -> list[str]:
    """Read the locations from recorded failures.

    Returns:
        The failure locations in recorded order.

    """
    return [location for location, _context in audit.failures]


def assert_rebuilt_session_matches(
    read_model: sessiondata_components.repository.session_data.SqliteSessionDataRepository,
    live_session: session_domain.session_state.SessionData,
) -> None:
    """Check that rebuilt session and actor facts match the live state."""
    rebuilt_session = session_fixtures.required_data(read_model)
    assert rebuilt_session.session == live_session.session
    assert rebuilt_session.actors == live_session.actors


def entry_types(read_model: sessiondata_components.repository.session_data.SqliteSessionDataRepository) -> list[str]:
    """Return the entry types in the fixed test session.

    Returns:
        The entry types in the fixed test session.

    """
    entries = read_model.entries_page(session_values.SESSION, limit=10).entries
    return [entry.entry_type for entry in entries]


def record_events(
    database: sessiondata_components.repository.connection.SqliteDatabase,
    events: sessiondata_components.repository.canonical_events.SqliteCanonicalEventRepository,
    payloads: Sequence[session_domain.event_base.EventPayload],
) -> None:
    """Process record.

    The facts, in the log, through the door they really arrive by: a recorded
        observation and the verdict reached about it.
    """
    recorder = sessiondata_components.repository.raw_events.SqliteRawEventRepository(database)
    for cursor, payload in enumerate(payloads, start=1):
        raw_event = sessiondata_components.harness.raw_events.RawEvent(
            raw_event_id=session_domain.ids.RawEventId(f"raw-{cursor}"),
            harness=CODEX_HARNESS,
            source_type="fixture",
            source_name="fixture.jsonl",
            source_position=str(cursor),
            session_id=session_values.SESSION,
            actor_id=folding.committed(payload, cursor=cursor).actor_id,
            parent_actor_id=None,
            observed_at=100.0,
            encoding="json",
            payload=b"{}",
        )
        recorder.record((raw_event,))
        events.record_translation(
            raw_event,
            "1",
            sessiondata_components.harness.raw_events.TranslationResult(
                (folding.committed(payload, cursor=cursor),),
                session_domain.records.RecordedTranslationDecision.TRANSLATED,
            ),
            time.time(),
        )
