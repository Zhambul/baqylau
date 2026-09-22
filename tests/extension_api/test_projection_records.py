# Copyright (c) 2026 Zhambyl Yermagambet
"""Check full record proposals, including create, replace, delete, and restore."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.record_changes import DeleteRecord
from baqylau_extension_api.models.records import DeletedRecord, MissingRecord, RecordState

from tests.extension_api import operation_samples, projection_checks, projection_samples

RECORD_CHANGES = "record_changes"


@pytest.mark.parametrize("prior_state", ["missing", "stored", "deleted"])
def test_projection_put_uses_captured_revision(prior_state: str) -> None:
    """Create or restore only with the complete captured prior state."""
    request = projection_samples.request()
    prior = _prior_record(prior_state)
    response = projection_samples.result()
    change = response.record_changes[0].model_copy(update={"expected_revision": prior.revision})
    response = response.model_copy(update={RECORD_CHANGES: (change,)})
    request = request.model_copy(update={"prior_records": (prior,)})
    assert projection_checks.validate_result(request, response) == response


def test_projection_delete_requires_stored_row() -> None:
    """Keep a typed delete separate from an empty or absent record."""
    request = projection_samples.request()
    row = projection_samples.stored_record()
    response = projection_samples.result().model_copy(update={
        RECORD_CHANGES: (DeleteRecord(key=row.key, expected_revision=row.revision),),
    })
    assert projection_checks.validate_result(request, response) == response
    marker = DeletedRecord(key=row.key, revision=row.revision, schema_ref=row.document.schema_ref)
    with pytest.raises(ExtensionContractError, match="stored row"):
        projection_checks.validate_result(request.model_copy(update={"prior_records": (marker,)}), response)


@pytest.mark.parametrize("change", [{"expected_revision": 0}, {"expected_revision": 7}, {
    "key": projection_samples.record_key().model_copy(update={"key": "unread"}),
}])
def test_projection_rejects_stale_record_write(change: dict[str, object]) -> None:
    """Reject a changed revision or an uncaptured key with no partial write."""
    request = projection_samples.request()
    before = request.model_dump_json()
    response = projection_samples.result()
    response = response.model_copy(update={
        RECORD_CHANGES: (response.record_changes[0].model_copy(update=change),),
    })
    with pytest.raises(ExtensionContractError, match="exact expected revision"):
        projection_checks.validate_result(request, response)
    assert request.model_dump_json() == before


def test_projection_rejects_duplicate_writes() -> None:
    """Do not let one result both replace and delete the same captured row."""
    response = projection_samples.result()
    put = response.record_changes[0]
    response = response.model_copy(update={
        RECORD_CHANGES: (put, DeleteRecord(key=put.key, expected_revision=put.expected_revision)),
    })
    with pytest.raises(ExtensionContractError, match="at most once"):
        projection_checks.validate_result(projection_samples.request(), response)


def test_projection_rejects_invalid_record_body() -> None:
    """Reject the complete result when a later record schema check fails."""
    response = projection_samples.result()
    change = response.record_changes[0].model_copy(update={
        "document": operation_samples.query_request("123").arguments,
    })
    with pytest.raises(ExtensionContractError):
        projection_checks.validate_result(
            projection_samples.request(), response.model_copy(update={RECORD_CHANGES: (change,)}),
        )


def _prior_record(prior_state: str) -> RecordState:
    row = projection_samples.stored_record()
    if prior_state == "missing":
        return MissingRecord(key=row.key)
    if prior_state == "deleted":
        return DeletedRecord(key=row.key, revision=row.revision, schema_ref=row.document.schema_ref)
    return row
