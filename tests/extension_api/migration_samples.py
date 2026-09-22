# Copyright (c) 2026 Zhambyl Yermagambet
"""Build migration declarations without importing the feature implementation."""

from baqylau_extension_api.manifest import contributions, data, metadata, migrations, settings
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.documents import EncodedDocument, SchemaDefinition
from baqylau_extension_api.models.migrations import MigrationBinding, RecordMigrationRequest, SettingsMigrationRequest
from baqylau_extension_api.models.records import RecordKey, StoredRecord
from baqylau_extension_api.models.scopes import InstallationScope, SnapshotCursor

from tests.extension_api import manifest_samples, samples

OWNER = "test.sample"
COLLECTION = f"{OWNER}.records"
OLD_SCHEMA = (
    '{"type":"object","properties":{"label":{"type":"string"}},'
    '"required":["label"],"additionalProperties":false}'
)
NEW_SCHEMA = (
    '{"type":"object","properties":{"title":{"type":"string"}},'
    '"required":["title"],"additionalProperties":false}'
)


def schema(version: int) -> SchemaDefinition:
    """Select one of the fixture's exact supported schema versions.

    Returns:
        An owned versioned definition with a checked byte digest.

    """
    definition = samples.schema_definition(OLD_SCHEMA if version == 1 else NEW_SCHEMA, "state")
    reference = definition.reference.model_copy(update={"owner": OWNER, "version": version})
    return definition.model_copy(update={"reference": reference})


def manifest(*, downgrade: bool = False) -> ExtensionManifest:
    """Declare both conversion kinds without enabling an undeclared reverse path.

    Returns:
        A package whose current schema is the explicitly selected target.

    """
    source, target = schema(1), schema(2)
    defaults = document(2, '{"title":"Default"}')
    if downgrade:
        source, target = target, source
        defaults = document(1, '{"label":"Default"}')
    return manifest_samples.backend_manifest(OWNER).model_copy(update={
        "backend": metadata.BackendEntry(module="migration_backend"), "capabilities": ("lifecycle", "migrations"),
        "schemas": (source, target),
        "settings": settings.SettingsDefinition(defaults=defaults, scopes=("installation",)),
        "contributions": contributions.Contributions(collections=(data.DocumentDefinition(
            name=COLLECTION, schema_ref=target.reference, scopes=("installation",),
        ),)),
        "migration_paths": (
            migrations.SettingsMigrationPath(source_schema=source.reference, target_schema=target.reference),
            migrations.RecordMigrationPath(
                source_schema=source.reference, target_schema=target.reference, collection=COLLECTION,
            ),
        ),
    })


def binding() -> MigrationBinding:
    """Select a candidate independently of any coding session.

    Returns:
        The exact prepared worker and migration call identity.

    """
    return MigrationBinding(
        extension_id=OWNER, scope=InstallationScope(), runtime_revision=samples.RUNTIME_REVISION,
        candidate_id="candidate-1", call_id="migration-call-1",
    )


def document(version: int, encoded: str) -> EncodedDocument:
    """Bind complete fixture data to its exact schema version.

    Returns:
        An encoded owned document, without decoding arbitrary JSON in the host.

    """
    return EncodedDocument(schema_ref=schema(version).reference, json_text=encoded)


def settings_request() -> SettingsMigrationRequest:
    """Capture version-one settings and revision before the candidate call.

    Returns:
        A complete request to convert settings into version two.

    """
    return SettingsMigrationRequest(
        binding=binding(), source_schema=schema(1).reference, target_schema=schema(2).reference,
        source_revision=3, source=document(1, '{"label":"Before"}'),
    )


def records_request() -> RecordMigrationRequest:
    """Capture two owned rows before one atomic candidate batch.

    Returns:
        Ordered rows with explicit revisions at one source snapshot.

    """
    return RecordMigrationRequest(
        binding=binding(), source_schema=schema(1).reference,
        target_schema=schema(2).reference, collection=COLLECTION,
        source_snapshot=SnapshotCursor(
            scope=InstallationScope(), history_revision="history-1", projection_generation="old", commit_cursor=7,
        ), records=tuple(StoredRecord(
            key=RecordKey(owner=OWNER, collection=COLLECTION, scope=InstallationScope(), key=key),
            revision=6, document=document(1, '{"label":"Before"}'), summary="Before conversion.",
        ) for key in ("first", "second")),
    )
