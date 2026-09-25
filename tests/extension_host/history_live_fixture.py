# Copyright (c) 2026 Zhambyl Yermagambet
"""Interpret and fold one finished Claude session live, so a test can reprocess it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from engine.react.loop import ReactionLoop, ReactionLoopDependencies
from engine.sessiondata import entries as session_entries
from repository.impl.sqlite import interpretations, raw_events, session_data
from tests import canonical_sessiondata_loop_models as loop_models, canonical_sessiondata_values as session_values
from tests.extension_host import lifecycle_pipeline_fixture as fixture
from tests.plugin_tests import translation_stage_fixture as native

if TYPE_CHECKING:
    from pathlib import Path

    from repository.impl.sqlite.connection import SqliteDatabase

PROMPT = "reprocess me"
SESSION_INPUTS = 2


def finished_session(tmp_path: Path) -> tuple[fixture.LifecycleCase, ReactionLoop]:
    """Interpret one prompt and the session end, record the session, and fold it.

    Returns:
        The live pipeline and the reaction loop that folded the read model.

    """
    pipeline = fixture.installed(tmp_path, native.claude_prompt(PROMPT))
    database = pipeline.pipeline.original.store.database
    raw_events.SqliteRawEventRepository(database).record((native.claude_hook("SessionEnd"),))
    assert pipeline.pipeline.batch().interpret_pending(pipeline.core.phase, bool) == SESSION_INPUTS
    _record_session(database)
    loop = ReactionLoop(ReactionLoopDependencies(
        canonical_fact_reader=interpretations.SqliteInterpretationRepository(database),
        session_data_repository=session_data.SqliteSessionDataRepository(database),
        reactions=(), session_entry_writer=session_entries.EntryWriter(), writers=session_values.WRITERS,
        listeners=(), harness_registry=loop_models.NoReactors(), harness_reactor_context=None,
        audit_recorder=loop_models.RecordingAudit(),
    ))
    loop.drain(bool)
    return pipeline, loop


def _record_session(database: SqliteDatabase) -> None:
    with database.write() as connection:
        row = connection.execute(
            "SELECT session_id, actor_id, harness FROM canonical_events WHERE event_type='session.started'",
        ).fetchone()
        connection.execute(
            "INSERT INTO sessions(session_id, lead_actor_id, harness, harness_session_id, source_reference, "
            "created_at) VALUES(?, ?, ?, ?, 'fixture', 1.0)",
            (row["session_id"], row["actor_id"], row["harness"], row["session_id"]),
        )
