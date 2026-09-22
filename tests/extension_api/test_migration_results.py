# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject stale candidate responses and incomplete or invalid migrated rows."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.migrations import results
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import migration_checks as checks, migration_samples as fixtures


@pytest.mark.parametrize("field", ["candidate_id", "call_id", "extension_id", "runtime_revision"])
def test_changed_result_binding_is_rejected(field: str) -> None:
    """A result cannot select another candidate or revive a stale worker call."""
    binding = fixtures.binding().model_copy(update={field: "changed"})
    with pytest.raises(ExtensionContractError, match="candidate call"):
        results.validate_settings_result(
            fixtures.settings_request(), checks.settings_ready().model_copy(update={"binding": binding}),
        )
    with pytest.raises(ExtensionContractError, match="candidate call"):
        results.validate_records_result(
            fixtures.records_request(), checks.records_ready().model_copy(update={"binding": binding}),
        )


def test_changed_settings_revision_is_rejected() -> None:
    """The result must retain the captured settings revision until a host check."""
    response = checks.settings_ready().model_copy(update={"source_revision": 99})
    with pytest.raises(ExtensionContractError, match="captured revision"):
        results.validate_settings_result(fixtures.settings_request(), response)


def test_changed_record_snapshot_is_rejected() -> None:
    """A worker cannot advance the storage cursor or choose another history."""
    response = checks.records_ready()
    snapshot = response.source_snapshot.model_copy(update={"projection_generation": "changed"})
    with pytest.raises(ExtensionContractError, match="captured snapshot"):
        results.validate_records_result(
            fixtures.records_request(), response.model_copy(update={"source_snapshot": snapshot}),
        )


@pytest.mark.parametrize("change", ["missing", "added", "reordered", "duplicate", "revision", "schema"])
def test_incomplete_or_changed_rows_are_rejected(change: str) -> None:
    """Migration is a complete conversion, not a general record write endpoint."""
    response = checks.records_ready()
    first, second = response.records
    changes = {
        "missing": (first,), "added": (first, second, first), "reordered": (second, first),
        "duplicate": (first, first),
        "revision": (first.model_copy(update={"expected_revision": 99}), second),
        "schema": (first.model_copy(update={"document": fixtures.document(1, '{"label":"Before"}')}), second),
    }
    with pytest.raises(ExtensionContractError):
        results.validate_records_result(
            fixtures.records_request(), response.model_copy(update={"records": changes[change]}),
        )


def test_target_documents_are_schema_checked() -> None:
    """Correct schema labels cannot hide an invalid candidate body."""
    response = checks.settings_ready().model_copy(update={
        "document": fixtures.document(2, '{"title":42}'),
    })
    with pytest.raises(ExtensionContractError):
        results.validate_migrated_documents(SchemaSet(fixtures.manifest().schemas), response)


def test_failed_batch_has_no_partial_values() -> None:
    """An explicit failure is valid without any candidate record writes."""
    response = checks.records_failed()
    assert results.validate_records_result(fixtures.records_request(), response) == response
    results.validate_migrated_documents(SchemaSet(fixtures.manifest().schemas), response)
