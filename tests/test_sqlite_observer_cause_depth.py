# Copyright (c) 2026 Zhambyl Yermagambet
"""Count the observer steps behind one committed fact."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.models.scopes import InstallationScope

from domain.ids import CanonicalEventId
from extensions.models.observations import ExtensionObservationMetadata
from repository.impl.sqlite.extension_observers import SqliteExtensionObserverRepository
from tests.extension_api import samples
from tests.extension_host import canonical_history_fixture as history

if TYPE_CHECKING:
    from repository.impl.sqlite.connection import SqliteDatabase

DEFAULT = "default"
CHAIN_STEPS = 3
SHORT_LIMIT = 2


def generated_fact(database: SqliteDatabase, event_id: str, cause: str) -> None:
    """Store one fact interpreted from an observation that names an earlier fact as its cause."""
    raw_event_id = f"raw-{event_id}"
    metadata = ExtensionObservationMetadata(
        observation_key=event_id, scope=InstallationScope(), schema_ref=samples.schema_definition().reference,
        occurred_at=None, causes=(cause,), runtime_revision="runtime-one",
    )
    history.insert_extension(database, event_id)
    with database.write() as connection:
        connection.execute(
            "INSERT INTO raw_events(raw_event_id, source_type, source_identity, source_name, source_position, "
            "observed_at, encoding, payload, extension_metadata) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (raw_event_id, "test.reader.line", "job:one", "job", event_id, 1.0, "json", b'"x"',
             metadata.model_dump_json()),
        )
        connection.execute(
            "INSERT INTO interpretation_events(event_id, raw_event_id, event_order, storage_result, history_revision) "
            "VALUES(?, ?, 0, 'accepted', ?)",
            (event_id, raw_event_id, DEFAULT),
        )


def chain(database: SqliteDatabase) -> str:
    """Store one source fact and three generated facts after it.

    Returns:
        The last generated fact.

    """
    history.insert_extension(database, "fact-0")
    for step in range(1, CHAIN_STEPS + 1):
        cause = step - 1
        generated_fact(database, f"fact-{step}", f"fact-{cause}")
    return f"fact-{CHAIN_STEPS}"


def test_cause_depth_counts_each_observer_step(main: SqliteDatabase) -> None:
    """Each generated fact adds one step; a source fact has none."""
    last = chain(main)
    repository = SqliteExtensionObserverRepository(main)

    assert repository.cause_depth(CanonicalEventId(last), DEFAULT, 10) == CHAIN_STEPS
    assert repository.cause_depth(CanonicalEventId("fact-0"), DEFAULT, 10) == 0


def test_cause_depth_stops_at_the_limit(main: SqliteDatabase) -> None:
    """The walk never reads more steps than the limit."""
    last = chain(main)

    repository = SqliteExtensionObserverRepository(main)

    assert repository.cause_depth(CanonicalEventId(last), DEFAULT, SHORT_LIMIT) == SHORT_LIMIT
