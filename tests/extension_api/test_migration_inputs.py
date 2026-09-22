# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid captured data before an extension migration callback runs."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.migrations import RecordMigrationRequest, SettingsMigrationRequest
from pydantic import ValidationError

from tests.extension_api import migration_checks, migration_samples as fixtures


def test_settings_and_records_are_captured() -> None:
    """Successful conversion returns candidates and leaves input values unchanged."""
    worker = migration_checks.worker()
    settings = fixtures.settings_request()
    records = fixtures.records_request()
    assert worker.migrate_settings(settings) == migration_checks.settings_ready()
    assert worker.migrate_records(records) == migration_checks.records_ready()
    assert settings.source == fixtures.document(1, '{"label":"Before"}')
    assert records.records == fixtures.records_request().records


@pytest.mark.parametrize("field", ["extension_id", "runtime_revision"])
def test_wrong_worker_binding_is_rejected(field: str) -> None:
    """A valid transport envelope cannot authorize a stale or foreign inner call."""
    settings = fixtures.settings_request()
    binding = settings.binding.model_copy(update={field: "wrong"})
    with pytest.raises(ExtensionContractError):
        migration_checks.worker().migrate_settings(settings.model_copy(update={"binding": binding}))
    with pytest.raises(ExtensionContractError):
        migration_checks.worker().migrate_records(
            fixtures.records_request().model_copy(update={"binding": binding}),
        )


@pytest.mark.parametrize("encoded", ['{"title":"Wrong version"}', '{"label":42}', '{"label":"ok","extra":1}'])
def test_invalid_source_settings_are_rejected(encoded: str) -> None:
    """Validate the old schema before the feature can receive the document."""
    request = fixtures.settings_request().model_copy(update={
        "source": fixtures.document(1, encoded),
    })
    with pytest.raises(ExtensionContractError):
        migration_checks.worker().migrate_settings(request)


@pytest.mark.parametrize("change", ["duplicate", "future", "scope", "owner", "collection", "schema"])
def test_invalid_captured_records_are_rejected(change: str) -> None:
    """Do not accept a row that is outside the captured collection snapshot."""
    request = fixtures.records_request()
    original = request.records[0]
    changes = {
        "duplicate": original,
        "future": original.model_copy(update={"revision": request.source_snapshot.commit_cursor + 1}),
        "scope": original.model_copy(update={
            "key": original.key.model_copy(update={"scope": None}),
        }),
        "owner": original.model_copy(update={
            "key": original.key.model_copy(update={"owner": "peer"}),
        }),
        "collection": original.model_copy(update={
            "key": original.key.model_copy(update={"collection": "test.sample.other"}),
        }),
        "schema": original.model_copy(update={"document": fixtures.document(2, '{"title":"Before"}')}),
    }
    records = (changes[change], request.records[1])
    if change == "duplicate":
        records = (original, original)
    with pytest.raises((ExtensionContractError, ValidationError)):
        migration_checks.worker().migrate_records(request.model_copy(update={"records": records}))


@pytest.mark.parametrize("revision", [-1, "3", True])
def test_settings_revision_is_strict(revision: object) -> None:
    """Captured revisions are nonnegative integers, not coerced text or booleans."""
    with pytest.raises(ValidationError):
        SettingsMigrationRequest.model_validate(fixtures.settings_request().model_copy(update={
            "source_revision": revision,
        }))


def test_credentials_are_not_request_fields() -> None:
    """A host must not transfer credential values as migration metadata."""
    with pytest.raises(ValidationError):
        SettingsMigrationRequest.model_validate(fixtures.settings_request().model_copy(update={
            "secret_values": "secret",
        }))


def test_empty_record_batch_is_not_a_migration() -> None:
    """No rows means the host has no conversion call to make."""
    with pytest.raises(ValidationError):
        RecordMigrationRequest.model_validate(fixtures.records_request().model_copy(update={"records": ()}))
