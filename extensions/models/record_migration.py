# Copyright (c) 2026 Zhambyl Yermagambet
"""Select the stored record collections that need a declared conversion before activation."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.migrations import RecordMigrationPath
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.base import ExtensionId, Identifier, WireModel
from baqylau_extension_api.models.documents import SchemaRef


class StoredRecordSchema(WireModel):
    """Name one schema that the live rows of an owner's collection use."""

    extension_id: ExtensionId
    collection: Identifier
    schema_ref: SchemaRef


class RecordSource(WireModel):
    """Pin one stored collection schema which the candidate package must convert."""

    collection: Identifier
    source_schema: SchemaRef


class RecordGeneration(WireModel):
    """Name the projection generation that holds one owner's converted records."""

    extension_id: ExtensionId
    generation: Identifier


def record_sources(
    manifest: ExtensionManifest, stored: tuple[StoredRecordSchema, ...],
) -> tuple[RecordSource, ...]:
    """Select stored schemas that differ from the package's current collection schemas.

    A collection that the package no longer declares keeps its rows unchanged;
    its stored summaries stay readable.

    Returns:
        One source per changed stored schema, in collection order.

    """
    return tuple(
        RecordSource(collection=entry.collection, source_schema=entry.schema_ref)
        for entry in stored
        if entry.extension_id == manifest.extension_id and _changed(manifest, entry)
    )


def validate_record_sources(manifest: ExtensionManifest, sources: tuple[RecordSource, ...]) -> None:
    """Require a migration backend and an exact declared path for each stored schema.

    Raises:
        ExtensionContractError: If the package cannot convert one of the stored schemas.

    """
    if sources and (manifest.backend is None or "migrations" not in manifest.capabilities):
        message = "record migration requires a backend with the migrations capability"
        raise ExtensionContractError(message)
    for source in sources:
        target = _target(manifest, source.collection)
        if target is None or RecordMigrationPath(
            source_schema=source.source_schema, target_schema=target, collection=source.collection,
        ) not in manifest.migration_paths:
            message = "the exact record migration path is not declared"
            raise ExtensionContractError(message)


def _changed(manifest: ExtensionManifest, stored: StoredRecordSchema) -> bool:
    target = _target(manifest, stored.collection)
    return target is not None and target != stored.schema_ref


def _target(manifest: ExtensionManifest, collection: str) -> SchemaRef | None:
    declared = (entry.schema_ref for entry in manifest.contributions.collections if entry.name == collection)
    return next(declared, None)
