# Copyright (c) 2026 Zhambyl Yermagambet
"""Run declared settings and record conversions through the pure worker lane."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.migrations import ExtensionMigrations
from baqylau_extension_api.migrations import requests, results
from baqylau_extension_api.models.migration_results import RecordMigrationResult, SettingsMigrationResult
from baqylau_extension_api.models.migrations import RecordMigrationRequest, SettingsMigrationRequest
from baqylau_extension_api.runtime import channel, codec, methods, revisions
from baqylau_extension_api.runtime.contract import RemoteCaller
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.schemas import SchemaSet


@dataclass(frozen=True)
class RemoteMigrations(ExtensionMigrations):
    """Call a migration worker without importing feature code into the host."""

    caller: RemoteCaller

    def migrate_settings(self, settings_request: SettingsMigrationRequest) -> SettingsMigrationResult:
        """Return checked candidate settings without updating active state.

        Returns:
            A complete conversion or a typed failure.

        """
        response = self.caller.invoke_typed(
            methods.MIGRATE_SETTINGS, settings_request, TypeAdapter[SettingsMigrationResult](SettingsMigrationResult),
        )
        return results.validate_settings_result(settings_request, response)

    def migrate_records(self, records_request: RecordMigrationRequest) -> RecordMigrationResult:
        """Return one checked candidate batch without committing record changes.

        Returns:
            Complete replacements or a typed batch failure.

        """
        response = self.caller.invoke_typed(
            methods.MIGRATE_RECORDS, records_request, TypeAdapter[RecordMigrationResult](RecordMigrationResult),
        )
        return results.validate_records_result(records_request, response)


@dataclass(frozen=True)
class WorkerMigrations(ExtensionMigrations):
    """Validate declarations and both schema versions around pure feature calls."""

    provider: ExtensionMigrations
    load: WorkerLoadRequest
    schemas: SchemaSet

    def migrate_settings(self, settings_request: SettingsMigrationRequest) -> SettingsMigrationResult:
        """Check source settings and every candidate output before reply.

        Returns:
            Schema-valid candidate settings or an explicit conversion failure.

        """
        request = requests.validate_settings_request(self.load.manifest, self.schemas, settings_request)
        revisions.require_runtime_revision(request.binding.runtime_revision, self.load.environment)
        response = results.validate_settings_result(request, self.provider.migrate_settings(request))
        results.validate_migrated_documents(self.schemas, response)
        return response

    def migrate_records(self, records_request: RecordMigrationRequest) -> RecordMigrationResult:
        """Check captured records before conversion and the complete batch after it.

        Returns:
            Checked candidate rows with their original keys and source revisions.

        """
        request = requests.validate_records_request(self.load.manifest, self.schemas, records_request)
        revisions.require_runtime_revision(request.binding.runtime_revision, self.load.environment)
        response = results.validate_records_result(request, self.provider.migrate_records(request))
        results.validate_migrated_documents(self.schemas, response)
        return response


def register_migrations(
    rpc: channel.RpcChannel, provider: ExtensionMigrations, request: WorkerLoadRequest, schemas: SchemaSet,
) -> None:
    """Keep both conversion methods in the pure lane with live callbacks blocked."""
    bound = WorkerMigrations(provider, request, schemas)
    rpc.register(methods.MIGRATE_SETTINGS, codec.ModelHandler(
        SettingsMigrationRequest, TypeAdapter(SettingsMigrationResult), bound.migrate_settings,
    ), "pure")
    rpc.register(methods.MIGRATE_RECORDS, codec.ModelHandler(
        RecordMigrationRequest, TypeAdapter(RecordMigrationResult), bound.migrate_records,
    ), "pure")
