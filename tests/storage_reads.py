# Copyright (c) 2026 Zhambyl Yermagambet
"""Inspect stored rows in tests through the production read and write functions.

Production code has no caller for these inspections, so they are not
repository methods. Each helper opens one transaction and calls the same
function that production storage uses.
"""

from __future__ import annotations

from operator import attrgetter
from typing import TYPE_CHECKING, cast

from engine.react import fold
from repository.impl.sqlite import (
    extension_lifecycle_reads,
    interpretation_reads,
    interpretations as interpretation_store,
    observation_codec,
    observations as observation_store,
    source_read_codec,
)

if TYPE_CHECKING:
    from baqylau_extension_api.models.scopes import ExtensionScope

    from domain import event_base, ids
    from extensions.models import interpretations, observations, runtime_candidates, source_reads
    from repository.impl.sqlite.canonical_events import SqliteCanonicalEventRepository
    from repository.impl.sqlite.connection import SqliteDatabase

    type CoreEvents = tuple[event_base.CanonicalEvent[event_base.EventPayload], ...]


MAX_PAGE = 1000


def _database(store: object) -> SqliteDatabase:
    """Read the database of any SQLite store that keeps it as `database`.

    Returns:
        The store's database.

    """
    database: SqliteDatabase = attrgetter("database")(store)
    return database


def append_observations(
    store: object, request: observations.ObservationAppend,
) -> observations.ObservationAppendOutcome:
    """Validate and append originals in one transaction, as a source or job settlement does.

    Returns:
        The new and repeated rows.

    """
    return observation_store.SqliteObservationRepository(_database(store)).append_observations(request)


def find_interpretation(
    store: object, history_revision: str, raw_event_id: ids.RawEventId,
) -> interpretations.InterpretationCommit | None:
    """Read one stored interpretation journal.

    Returns:
        The journal, or None.

    """
    with _database(store).read() as connection:
        return interpretation_reads.read_journal(connection, history_revision, raw_event_id)


def find_source_read(store: object, runtime_revision: str, call_id: str) -> source_reads.SourceReadCommit | None:
    """Read one stored source call.

    Returns:
        The accepted call, or None.

    """
    with _database(store).read() as connection:
        return source_read_codec.read_call(connection, runtime_revision, call_id)


def read_extension_runtime(store: object, runtime_revision: str) -> runtime_candidates.RuntimeCandidate | None:
    """Read one reserved runtime selection.

    Returns:
        The selection, or None.

    """
    with _database(store).read() as connection:
        return extension_lifecycle_reads.read_runtime(connection, runtime_revision)


def page_from(store: object, cursor: int, limit: int) -> CoreEvents:
    """Read the current core facts after a cursor through the production mixed page.

    Returns:
        The core events in accepted order.

    """
    canonical = cast("SqliteCanonicalEventRepository", store)
    reader = interpretation_store.SqliteInterpretationRepository(canonical.sqlite_database)
    selected: CoreEvents = ()
    while len(selected) < limit:
        facts = reader.current_fact_page(cursor, MAX_PAGE).facts
        if not facts:
            break
        selected += fold.core_events(facts)
        cursor = facts[-1].cursor
    return selected[:limit]


def observations_for_scope(
    store: object, scope: ExtensionScope, after_cursor: int, limit: int,
) -> tuple[observations.StoredObservation, ...]:
    """Read the stored originals of one exact scope, for test inspection only.

    Returns:
        The originals after the cursor, in arrival order.

    """
    with _database(store).read() as connection:
        rows = connection.execute(
            "SELECT * FROM raw_events WHERE scope=? AND id>? ORDER BY id LIMIT ?",
            (scope.model_dump_json(), after_cursor, limit),
        ).fetchall()
    return tuple(observation_codec.stored_observation(row) for row in rows)
