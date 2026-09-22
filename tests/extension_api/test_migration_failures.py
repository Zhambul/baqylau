# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep rejected values and oversized batches away from candidate commits."""

import asyncio
from pathlib import Path

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.migrations import common, requests, results
from baqylau_extension_api.models import migration_results as outcomes
from baqylau_extension_api.models.migrations import MAX_MIGRATION_ROWS, RecordMigrationRequest
from baqylau_extension_api.schemas import SchemaSet
from pydantic import ValidationError

from tests.extension_api import migration_checks as checks, migration_process, migration_samples as fixtures

SIZE_ERROR = "encoded size limit"


def test_failed_settings_have_no_partial_value(tmp_path: Path) -> None:
    """The worker can report a known conversion failure without ending its process."""
    response = asyncio.run(_rejected_value(tmp_path))
    assert isinstance(response, outcomes.SettingsMigrationFailed)
    assert response.diagnostic.code == "unsupported_value"
    assert "document" not in response.model_fields_set


async def _rejected_value(directory: Path) -> outcomes.SettingsMigrationResult:
    request = fixtures.settings_request().model_copy(update={
        "source": fixtures.document(1, '{"label":"reject"}'),
    })
    async with migration_process.running_migrations(directory) as proxy:
        return await asyncio.to_thread(proxy.migrate_settings, request)


@pytest.mark.parametrize("field", ["document", "records"])
def test_failure_cannot_include_partial_output(field: str) -> None:
    """The failed result branch has no field for partial candidate writes."""
    response = checks.records_failed().model_copy(update={field: ()})
    with pytest.raises(ValidationError):
        results.validate_records_result(fixtures.records_request(), response)


def test_request_and_response_size_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enforce the complete message bounds in addition to individual field limits."""
    monkeypatch.setattr(common, "MAX_MIGRATION_REQUEST_BYTES", 1)
    monkeypatch.setattr(common, "MAX_MIGRATION_RESULT_BYTES", 1)
    manifest = fixtures.manifest()
    with pytest.raises(ExtensionContractError, match=SIZE_ERROR):
        requests.validate_settings_request(manifest, SchemaSet(manifest.schemas), fixtures.settings_request())
    with pytest.raises(ExtensionContractError, match=SIZE_ERROR):
        requests.validate_records_request(manifest, SchemaSet(manifest.schemas), fixtures.records_request())
    with pytest.raises(ExtensionContractError, match=SIZE_ERROR):
        results.validate_settings_result(fixtures.settings_request(), checks.settings_ready())
    with pytest.raises(ExtensionContractError, match=SIZE_ERROR):
        results.validate_records_result(fixtures.records_request(), checks.records_ready())


def test_record_count_is_bounded() -> None:
    """Oversized row lists are rejected before schema validation or feature calls."""
    request = fixtures.records_request()
    with pytest.raises(ValidationError):
        RecordMigrationRequest.model_validate(request.model_copy(update={
            "records": (request.records[0],) * (MAX_MIGRATION_ROWS + 1),
        }))
