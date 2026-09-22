# Copyright (c) 2026 Zhambyl Yermagambet
"""Own pure settings and record conversions in a separate feature package."""

from baqylau_extension_api.contracts import lifecycle, migrations, plugin, services
from baqylau_extension_api.models import directory, lifecycle as lifecycle_models, migration_results as outcomes
from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.models.documents import Diagnostic, EncodedDocument, SchemaRef
from baqylau_extension_api.models.migrations import RecordMigrationRequest, SettingsMigrationRequest
from baqylau_extension_api.models.record_changes import PutRecord


class OldValue(WireModel):
    """Represent a version-one feature value."""

    label: str


class NewValue(WireModel):
    """Represent the renamed field in version two."""

    title: str


def converted(source: EncodedDocument, target: SchemaRef) -> EncodedDocument:
    """Convert only through declared feature models, not open JSON dictionaries.

    Returns:
        The exact requested target document.

    """
    if source.json_text == '{"label":"invalid"}':
        return EncodedDocument(schema_ref=target, json_text='{"title":42}')
    if target.version == 1:
        encoded = OldValue(label=NewValue.model_validate_json(source.json_text).title).model_dump_json()
    else:
        encoded = NewValue(title=OldValue.model_validate_json(source.json_text).label).model_dump_json()
    return EncodedDocument(schema_ref=target, json_text=encoded)


class MigrationExample(plugin.ExtensionPlugin, lifecycle.ExtensionLifecycle, migrations.ExtensionMigrations):
    """Keep both pure conversions and package lifetime behind public protocols."""

    def __init__(self, host_services: services.ExtensionHostServices) -> None:
        """Keep the public service facade to test the worker's pure-call guard."""
        self._services = host_services

    @property
    def extension_info(self) -> lifecycle_models.ExtensionInfo:
        """The exact package identity selected by the host."""
        return self._services.environment.extension_info

    @property
    def capabilities(self) -> plugin.ExtensionCapabilities:
        """The lifecycle and migration capabilities supplied by this package."""
        return plugin.ExtensionCapabilities(lifecycle=self, migrations=self)

    def activate(self, request: lifecycle_models.ActivationRequest) -> lifecycle_models.ActivationResult:
        """Prepare the fixture without changing stored user data.

        Returns:
            Readiness for the selected runtime.

        """
        return lifecycle_models.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle_models.DeactivationRequest) -> lifecycle_models.DeactivationResult:
        """Release the fixture without live jobs.

        Returns:
            A complete shutdown result.

        """
        return lifecycle_models.DeactivationResult(runtime_revision=request.runtime_revision)

    def migrate_settings(self, settings_request: SettingsMigrationRequest) -> outcomes.SettingsMigrationResult:
        """Convert a captured value or return a complete typed failure.

        Returns:
            Candidate settings without any storage write.

        """
        request = settings_request
        if request.source.json_text == '{"label":"host_call"}':
            self._services.directory.list_extensions(directory.DirectoryRequest(active_only=True))
        if request.source.json_text == '{"label":"reject"}':
            return outcomes.SettingsMigrationFailed(
                binding=request.binding, source_revision=request.source_revision,
                diagnostic=Diagnostic(code="unsupported_value", message="The old value cannot be converted."),
            )
        return outcomes.SettingsMigrationReady(
            binding=request.binding, source_revision=request.source_revision,
            document=converted(request.source, request.target_schema),
        )

    def migrate_records(self, records_request: RecordMigrationRequest) -> outcomes.RecordMigrationResult:
        """Convert all captured rows without changing their identity or revision.

        Returns:
            A complete ordered candidate batch.

        """
        if records_request.records[0].document.json_text == '{"label":"host_call"}':
            self._services.directory.list_extensions(directory.DirectoryRequest(active_only=True))
        if records_request.records[0].document.json_text == '{"label":"reject"}':
            return outcomes.RecordMigrationFailed(
                binding=records_request.binding, source_snapshot=records_request.source_snapshot,
                diagnostic=Diagnostic(code="unsupported_value", message="The old record cannot be converted."),
            )
        return outcomes.RecordMigrationReady(
            binding=records_request.binding, source_snapshot=records_request.source_snapshot,
            records=tuple(PutRecord(
                key=record.key, expected_revision=record.revision,
                document=converted(record.document, records_request.target_schema), summary="Converted value.",
            ) for record in records_request.records),
        )


def build_extension(services: services.ExtensionHostServices) -> plugin.ExtensionPlugin:
    """Construct the external migration package only in the worker process.

    Returns:
        Its explicit public capabilities.

    """
    return MigrationExample(services)


FACTORY: plugin.ExtensionFactory = build_extension
