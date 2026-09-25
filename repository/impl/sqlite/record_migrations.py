# Copyright (c) 2026 Zhambyl Yermagambet
"""Read stored record schemas and hold one owner's converted rows in a migrating generation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from baqylau_extension_api.models import records, scopes
from pydantic import TypeAdapter

from core.work_queue import WorkKind
from extensions.models.record_migration import RecordGeneration, StoredRecordSchema
from repository.contract.record_migrations import StaleRecordPage
from repository.impl.sqlite import connection, extension_records, record_migration_copy, scope_heads

if TYPE_CHECKING:
    from baqylau_extension_api.models.record_changes import PutRecord

    from extensions.models.record_migration import RecordSource

_SCOPE_ADAPTER: TypeAdapter[scopes.ExtensionScope] = TypeAdapter(scopes.ExtensionScope)
_STALE_SCOPES_SQL = (
    "SELECT DISTINCT scope FROM extension_candidate_records WHERE generation=? AND owner=? AND collection=? "
    "AND schema_ref=? AND state='stored' ORDER BY scope"
)
_STALE_PAGE_SQL = (
    "SELECT record_key, state, revision, schema_ref, document, summary FROM extension_candidate_records "
    "WHERE generation=? AND owner=? AND collection=? AND schema_ref=? AND state='stored' AND scope=? "
    "ORDER BY record_key LIMIT ?"
)


@dataclass(frozen=True)
class SqliteRecordMigrationStore:
    """Keep conversion input and output in mirror rows until the lifecycle commit switches the owner's head."""

    database: connection.SqliteDatabase

    def stored_record_schemas(self) -> tuple[StoredRecordSchema, ...]:
        """Read each distinct owner, collection, and schema of the live stored rows.

        Returns:
            The stored schemas in owner and collection order.

        """
        with self.database.read() as connection_handle:
            return record_migration_copy.stored_schemas(connection_handle)

    def start(self, owner: str) -> RecordGeneration:
        """Copy the live records, feed rows, and cursors of one owner into a new migrating generation.

        Returns:
            The owner and its new generation.

        """
        record_generation = RecordGeneration(extension_id=owner, generation=f"migration-{uuid4().hex}")
        with self.database.write(WorkKind.CANONICAL, notify_readers=False) as connection_handle:
            record_migration_copy.copy_live(connection_handle, owner, record_generation.generation)
        return record_generation

    def stale_scopes(
        self, record_generation: RecordGeneration, record_source: RecordSource,
    ) -> tuple[scopes.ExtensionScope, ...]:
        """Read the scopes that still have rows in the source schema.

        Returns:
            The scopes in stored order.

        """
        with self.database.read() as connection_handle:
            rows = connection_handle.execute(
                _STALE_SCOPES_SQL, _stale_values(record_generation, record_source),
            ).fetchall()
        return tuple(_SCOPE_ADAPTER.validate_json(row["scope"]) for row in rows)

    def stale_page(
        self,
        record_generation: RecordGeneration,
        record_source: RecordSource,
        scope: scopes.ExtensionScope,
        limit: int,
    ) -> StaleRecordPage:
        """Read the next rows of one scope that are still in the source schema.

        Returns:
            The rows in key order and the scope's copied projection cursor.

        """
        scope_text = scope.model_dump_json()
        with self.database.read() as connection_handle:
            rows = connection_handle.execute(
                _STALE_PAGE_SQL, (*_stale_values(record_generation, record_source), scope_text, limit),
            ).fetchall()
            cursor = scope_heads.consumer_cursor(
                connection_handle, scope_heads.ConsumerCursorTable.PROJECTION,
                (record_generation.extension_id, scope_text, "default", record_generation.generation),
            )
        states = (
            extension_records.row_state(record_generation.extension_id, record_source.collection, scope, row)
            for row in rows
        )
        return StaleRecordPage(
            commit_cursor=cursor, records=tuple(state for state in states if isinstance(state, records.StoredRecord)),
        )

    def replace(self, record_generation: RecordGeneration, converted: tuple[PutRecord, ...]) -> None:
        """Replace converted rows at their captured revisions.

        A row that changed after it was captured fails the whole write.
        """
        with self.database.write(notify_readers=False) as connection_handle:
            for record in converted:
                record_migration_copy.replace_row(connection_handle, record_generation.generation, record)

    def fail(self, record_generation: RecordGeneration) -> None:
        """Mark a migrating generation failed and remove its copied rows."""
        with self.database.write(notify_readers=False) as connection_handle:
            record_migration_copy.discard(connection_handle, record_generation.generation)


def _stale_values(record_generation: RecordGeneration, record_source: RecordSource) -> tuple[str, str, str, str]:
    return (
        record_generation.generation, record_generation.extension_id, record_source.collection,
        record_source.source_schema.model_dump_json(),
    )
