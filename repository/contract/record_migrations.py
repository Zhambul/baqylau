# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the storage that a record migration reads and writes before package activation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from baqylau_extension_api.models.records import StoredRecord

if TYPE_CHECKING:
    from baqylau_extension_api.models.record_changes import PutRecord
    from baqylau_extension_api.models.scopes import ExtensionScope

    from extensions.models.record_migration import RecordGeneration, RecordSource, StoredRecordSchema


@dataclass(frozen=True)
class StaleRecordPage:
    """Keep one scope's rows in a source schema with the scope's captured projection cursor."""

    commit_cursor: int
    records: tuple[StoredRecord, ...]


class StoredRecordSchemaReader(Protocol):
    """Read the schemas that live extension records use, for lifecycle planning."""

    def stored_record_schemas(self) -> tuple[StoredRecordSchema, ...]:
        """Read each distinct owner, collection, and schema of the live stored rows."""
        ...


class RecordMigrationStore(Protocol):
    """Copy one owner's live rows into a migrating generation and replace its converted rows."""

    def start(self, owner: str) -> RecordGeneration:
        """Copy the live records, feed rows, and cursors of one owner into a new migrating generation."""
        ...

    def stale_scopes(
        self, record_generation: RecordGeneration, record_source: RecordSource,
    ) -> tuple[ExtensionScope, ...]:
        """Read the scopes that still have rows in the source schema."""
        ...

    def stale_page(
        self, record_generation: RecordGeneration, record_source: RecordSource, scope: ExtensionScope, limit: int,
    ) -> StaleRecordPage:
        """Read the next rows of one scope that are still in the source schema."""
        ...

    def replace(self, record_generation: RecordGeneration, converted: tuple[PutRecord, ...]) -> None:
        """Replace converted rows at their captured revisions."""
        ...

    def fail(self, record_generation: RecordGeneration) -> None:
        """Mark a migrating generation failed and remove its copied rows."""
        ...
