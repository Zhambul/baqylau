# Copyright (c) 2026 Zhambyl Yermagambet
"""Convert one owner's stored records into a migrating generation through the pure migration protocol."""

from dataclasses import dataclass
from threading import Event

from baqylau_extension_api.contracts.migrations import ExtensionMigrations
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.migrations import requests, results
from baqylau_extension_api.models import documents, migrations as migration_models, record_changes, scopes
from baqylau_extension_api.schemas import SchemaSet

from extensions.models.record_migration import RecordGeneration, RecordSource
from extensions.runtime_preparation_contract import RuntimePreparationError, require_running
from repository.contract.record_migrations import RecordMigrationStore


@dataclass(frozen=True)
class RecordMigration:
    """Own one pure record conversion context; only the migration store sees the copied rows."""

    manifest: ExtensionManifest
    provider: ExtensionMigrations
    runtime_revision: str
    store: RecordMigrationStore
    stop_requested: Event | None

    def migrate(self, record_sources: tuple[RecordSource, ...]) -> RecordGeneration:
        """Copy the owner's live rows, then convert each stale scope until no row is in a source schema.

        Any failure fails the generation and keeps the live rows.

        Returns:
            The migrating generation that the lifecycle commit makes live.

        """
        record_generation = self.store.start(self.manifest.extension_id)
        try:
            for record_source in record_sources:
                for scope in self.store.stale_scopes(record_generation, record_source):
                    self._convert_scope(record_generation, record_source, scope)
        except Exception:
            self.store.fail(record_generation)
            raise
        return record_generation

    def _convert_scope(
        self, record_generation: RecordGeneration, record_source: RecordSource, scope: scopes.ExtensionScope,
    ) -> None:
        call = 0
        page = self.store.stale_page(record_generation, record_source, scope, migration_models.MAX_MIGRATION_ROWS)
        while page.records:
            require_running(self.stop_requested)
            request = migration_models.RecordMigrationRequest(
                binding=migration_models.MigrationBinding(
                    extension_id=self.manifest.extension_id, scope=scope, runtime_revision=self.runtime_revision,
                    candidate_id=record_generation.generation, call_id=f"records-{call}",
                ),
                source_schema=record_source.source_schema, target_schema=_target(self.manifest, record_source),
                collection=record_source.collection,
                source_snapshot=scopes.SnapshotCursor(
                    scope=scope, history_revision="default", projection_generation=record_generation.generation,
                    commit_cursor=page.commit_cursor,
                ),
                records=page.records,
            )
            self.store.replace(record_generation, self._converted(request))
            call += 1
            page = self.store.stale_page(record_generation, record_source, scope, migration_models.MAX_MIGRATION_ROWS)

    def _converted(self, request: migration_models.RecordMigrationRequest) -> tuple[record_changes.PutRecord, ...]:
        schemas = SchemaSet(self.manifest.schemas)
        checked = requests.validate_records_request(self.manifest, schemas, request)
        response = results.validate_records_result(checked, self.provider.migrate_records(checked))
        results.validate_migrated_documents(schemas, response)
        if response.status != "ready":
            message = "the extension could not convert candidate records"
            raise RuntimePreparationError(message)
        return response.records


def _target(manifest: ExtensionManifest, record_source: RecordSource) -> documents.SchemaRef:
    return next(
        collection.schema_ref for collection in manifest.contributions.collections
        if collection.name == record_source.collection
    )
