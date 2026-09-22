# Copyright (c) 2026 Zhambyl Yermagambet
"""Use the existing raw table and queue for both observation branches."""

from baqylau_extension_api.models.scopes import ExtensionScope
from pydantic import StrictInt, TypeAdapter

from core.work_queue import WorkKind
from domain.ids import RawEventId
from extensions.models.observations import ObservationAppend, ObservationAppendOutcome, StoredObservation
from repository.contract.observations import ObservationRepository
from repository.impl.sqlite import observation_codec as codec, observation_validation, observation_writes
from repository.impl.sqlite.connection import SqliteDatabase

MAX_OBSERVATION_PAGE = 1000


class SqliteObservationRepository(ObservationRepository):
    """Keep complete append validation and pending input writes in one transaction."""

    def __init__(self, sqlite_database: SqliteDatabase) -> None:
        """Use the application's existing database and work notices."""
        self.database = sqlite_database

    def append_observations(self, request: ObservationAppend) -> ObservationAppendOutcome:
        """Validate the complete input before any row is accepted.

        Returns:
            Original new and repeated rows after commit.

        """
        checked = ObservationAppend.model_validate(request)
        with self.database.write(WorkKind.RAW, notify_readers=False) as connection:
            observation_validation.validate_append(connection, checked)
            return observation_writes.append(connection, checked)

    def find_observation(self, raw_event_id: RawEventId) -> StoredObservation | None:
        """Read either input branch without requiring an active extension.

        Returns:
            The original observation, or no row for an unknown ID.

        """
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM raw_events WHERE raw_event_id=?", (raw_event_id,)).fetchone()
        return None if row is None else codec.stored_observation(row)

    def pending_observations(self, limit: int) -> tuple[StoredObservation, ...]:
        """Read both branches from the same ordered pending queue.

        Returns:
            A bounded batch of original observations in arrival order.

        """
        _validate_page(0, limit)
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT raw_events.* FROM pending_raw_events "
                "JOIN raw_events ON raw_events.id=pending_raw_events.raw_event_row_id "
                "ORDER BY pending_raw_events.raw_event_row_id LIMIT ?", (limit,),
            ).fetchall()
        return tuple(codec.stored_observation(row) for row in rows)

    def observations_for_scope(
        self, scope: ExtensionScope, after_cursor: int, limit: int,
    ) -> tuple[StoredObservation, ...]:
        """Use the indexed complete scope, including repository worktree identity.

        Returns:
            Original observations after the selected arrival cursor.

        """
        _validate_page(after_cursor, limit)
        selected = TypeAdapter[ExtensionScope](ExtensionScope).validate_python(scope)
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM raw_events WHERE scope=? AND id>? ORDER BY id LIMIT ?",
                (selected.model_dump_json(), after_cursor, limit),
            ).fetchall()
        return tuple(codec.stored_observation(row) for row in rows)


def _validate_page(after_cursor: int, limit: int) -> None:
    checked_cursor = TypeAdapter[StrictInt](StrictInt).validate_python(after_cursor)
    checked_limit = TypeAdapter[StrictInt](StrictInt).validate_python(limit)
    if checked_cursor < 0 or not 1 <= checked_limit <= MAX_OBSERVATION_PAGE:
        message = "observation cursor and page limit are invalid"
        raise ValueError(message)
