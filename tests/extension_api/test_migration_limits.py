# Copyright (c) 2026 Zhambyl Yermagambet
"""Exercise the real encoded migration bounds with individually valid rows."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.migrations import requests, results
from baqylau_extension_api.models.record_changes import PutRecord
from baqylau_extension_api.models.records import StoredRecord
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import migration_checks as checks, migration_samples as fixtures

LARGE_TEXT_LENGTH = 1_000_000
REQUEST_ROWS = 9
RESPONSE_ROWS = 5


def test_complete_request_has_real_byte_bound() -> None:
    """Nine valid documents cannot bypass the eight MiB request boundary."""
    request = fixtures.records_request().model_copy(update={"records": _large_source_rows(REQUEST_ROWS)})
    manifest = fixtures.manifest()
    with pytest.raises(ExtensionContractError, match="encoded size limit"):
        requests.validate_records_request(manifest, SchemaSet(manifest.schemas), request)


def test_complete_response_has_real_byte_bound() -> None:
    """A valid input batch cannot cause an unbounded candidate reply."""
    request = fixtures.records_request().model_copy(update={"records": _large_source_rows(RESPONSE_ROWS)})
    content = "x" * LARGE_TEXT_LENGTH
    document = fixtures.document(2, f'{{"title":"{content}"}}')
    rows = tuple(PutRecord(
        key=record.key, expected_revision=record.revision,
        document=document, summary="Converted value.",
    ) for record in request.records)
    response = checks.records_ready().model_copy(update={"records": rows})
    with pytest.raises(ExtensionContractError, match="encoded size limit"):
        results.validate_records_result(request, response)


def _large_source_rows(count: int) -> tuple[StoredRecord, ...]:
    original = fixtures.records_request().records[0]
    content = "x" * LARGE_TEXT_LENGTH
    document = fixtures.document(1, f'{{"label":"{content}"}}')
    return tuple(original.model_copy(update={
        "key": original.key.model_copy(update={"key": f"record-{index}"}), "document": document,
    }) for index in range(count))
