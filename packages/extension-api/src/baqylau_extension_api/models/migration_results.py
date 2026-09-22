# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep complete candidate migration values separate from failed conversions."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import Revision, WireModel
from baqylau_extension_api.models.documents import Diagnostic, EncodedDocument
from baqylau_extension_api.models.migrations import MAX_MIGRATION_ROWS, MigrationBinding
from baqylau_extension_api.models.record_changes import PutRecord
from baqylau_extension_api.models.scopes import SnapshotCursor


class SettingsMigrationOutcome(WireModel):
    """Repeat the candidate and captured settings revision on every outcome."""

    binding: MigrationBinding
    source_revision: Revision


class SettingsMigrationReady(SettingsMigrationOutcome):
    """Return a complete candidate document, without committing it."""

    status: Literal["ready"] = "ready"
    document: EncodedDocument


class SettingsMigrationFailed(SettingsMigrationOutcome):
    """Report a failed conversion without returning partial candidate settings."""

    status: Literal["failed"] = "failed"
    diagnostic: Diagnostic


class RecordMigrationOutcome(WireModel):
    """Keep every outcome bound to the same candidate and source snapshot."""

    binding: MigrationBinding
    source_snapshot: SnapshotCursor


class RecordMigrationReady(RecordMigrationOutcome):
    """Return one replacement for each captured row, in the supplied order."""

    status: Literal["ready"] = "ready"
    records: Annotated[tuple[PutRecord, ...], Field(min_length=1, max_length=MAX_MIGRATION_ROWS)]


class RecordMigrationFailed(RecordMigrationOutcome):
    """Report a failed batch without returning partial record writes."""

    status: Literal["failed"] = "failed"
    diagnostic: Diagnostic


type SettingsMigrationResult = Annotated[
    SettingsMigrationReady | SettingsMigrationFailed, Field(discriminator="status"),
]
type RecordMigrationResult = Annotated[
    RecordMigrationReady | RecordMigrationFailed, Field(discriminator="status"),
]
