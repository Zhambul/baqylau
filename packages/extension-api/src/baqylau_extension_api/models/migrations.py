# Copyright (c) 2026 Zhambyl Yermagambet
"""Capture migration input without giving an extension active storage access."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.models.base import ExtensionId, Identifier, Revision, WireModel
from baqylau_extension_api.models.documents import EncodedDocument, SchemaRef
from baqylau_extension_api.models.records import StoredRecord
from baqylau_extension_api.models.scopes import ExtensionScope, SnapshotCursor

MAX_MIGRATION_ROWS = 1000


class MigrationBinding(WireModel):
    """Name one call against an inactive candidate, not an active storage head."""

    extension_id: ExtensionId
    scope: ExtensionScope
    runtime_revision: Identifier
    candidate_id: Identifier
    call_id: Identifier


class MigrationRequest(WireModel):
    """Select the exact source and target schemas of a declared conversion."""

    binding: MigrationBinding
    source_schema: SchemaRef
    target_schema: SchemaRef


class SettingsMigrationRequest(MigrationRequest):
    """Supply captured settings without credential values or active write access."""

    source_revision: Revision
    source: EncodedDocument


class RecordMigrationRequest(MigrationRequest):
    """Supply one ordered batch from a captured collection snapshot."""

    collection: Identifier
    source_snapshot: SnapshotCursor
    records: Annotated[tuple[StoredRecord, ...], Field(min_length=1, max_length=MAX_MIGRATION_ROWS)]
