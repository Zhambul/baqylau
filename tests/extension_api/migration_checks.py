# Copyright (c) 2026 Zhambyl Yermagambet
"""Build typed candidate outcomes independently of the feature implementation."""

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.models import migration_results as outcomes
from baqylau_extension_api.models.documents import Diagnostic
from baqylau_extension_api.models.record_changes import PutRecord
from baqylau_extension_api.runtime.migrations import WorkerMigrations
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import migration_example, migration_samples, samples
from tests.extension_api.test_protocols import SampleDirectory


def settings_ready() -> outcomes.SettingsMigrationReady:
    """Return expected settings output without calling a provider.

    Returns:
        The exact captured revision and new schema.

    """
    request = migration_samples.settings_request()
    return outcomes.SettingsMigrationReady(
        binding=request.binding, source_revision=request.source_revision,
        document=migration_samples.document(2, '{"title":"Before"}'),
    )


def records_ready() -> outcomes.RecordMigrationReady:
    """Return the complete expected record batch in its captured order.

    Returns:
        One candidate replacement for every source row.

    """
    request = migration_samples.records_request()
    return outcomes.RecordMigrationReady(
        binding=request.binding, source_snapshot=request.source_snapshot,
        records=tuple(PutRecord(
            key=record.key, expected_revision=record.revision,
            document=migration_samples.document(2, '{"title":"Before"}'), summary="Converted value.",
        ) for record in request.records),
    )


def worker() -> WorkerMigrations:
    """Use the same validation adapter as the real worker transport.

    Returns:
        A checked local wrapper for focused invalid-input cases.

    """
    load = WorkerLoadRequest(manifest=migration_samples.manifest(), environment=samples.worker_environment())
    provider = migration_example.MigrationExample(ExtensionHostServices(SampleDirectory(), load.environment))
    return WorkerMigrations(provider, load, SchemaSet(load.manifest.schemas))


def records_failed() -> outcomes.RecordMigrationFailed:
    """Return an explicit failure with no partial write list.

    Returns:
        A failed outcome at the unchanged source boundary.

    """
    request = migration_samples.records_request()
    return outcomes.RecordMigrationFailed(
        binding=request.binding, source_snapshot=request.source_snapshot,
        diagnostic=Diagnostic(code="unsupported_value", message="The old record cannot be converted."),
    )
