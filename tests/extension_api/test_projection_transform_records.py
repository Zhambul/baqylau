# Copyright (c) 2026 Zhambyl Yermagambet
"""Check record changes after projection transforms without a storage write."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.projection_changes import ProjectionChange
from baqylau_extension_api.models.record_changes import DeleteRecord
from baqylau_extension_api.models.transforms import Drop, Replace

from tests.extension_api import (
    operation_samples,
    projection_transform_checks as checks,
    projection_transform_samples as fixtures,
)

WRITE_FIELD = "write"


def test_transform_replaces_owned_record_body() -> None:
    """Keep the captured key and revision while changing valid document content."""
    original = fixtures.record_change()
    replacement = original.model_copy(update={WRITE_FIELD: original.write.model_copy(update={
        "document": operation_samples.query_request('"changed"').arguments,
    })})
    output = checks.apply(fixtures.request(), Replace[ProjectionChange](
        input_id=original.change_id, document=replacement,
    ))
    assert output[-1] == replacement


def test_transform_can_replace_put_with_delete() -> None:
    """Delete a captured row through an explicit typed write, not a null document."""
    original = fixtures.record_change()
    replacement = original.model_copy(update={WRITE_FIELD: DeleteRecord(
        key=original.write.key, expected_revision=original.write.expected_revision,
    )})
    output = checks.apply(fixtures.request(), Replace[ProjectionChange](
        input_id=original.change_id, document=replacement,
    ))
    assert output[-1] == replacement


def test_transform_drop_keeps_the_prior_record() -> None:
    """Suppress a proposed write without turning that suppression into a delete."""
    request = fixtures.request()
    output = checks.apply(request, Drop(input_id="record-change", reason="Keep old record"))
    assert output == request.changes[:-1]
    assert request.prior_records == fixtures.request().prior_records


@pytest.mark.parametrize("change", [{"expected_revision": 0}, {"expected_revision": 7}])
def test_transform_cannot_change_record_revision(change: dict[str, object]) -> None:
    """Reject an attempt to move a write away from its captured precondition."""
    original = fixtures.record_change()
    replacement = original.model_copy(update={WRITE_FIELD: original.write.model_copy(update=change)})
    with pytest.raises(ExtensionContractError, match="expected revision"):
        checks.apply(fixtures.request(), Replace[ProjectionChange](
            input_id=original.change_id, document=replacement,
        ))


def test_transform_rejects_bad_record_schema() -> None:
    """Reject the whole result when a later changed record has invalid content."""
    original = fixtures.record_change()
    replacement = original.model_copy(update={WRITE_FIELD: original.write.model_copy(update={
        "document": operation_samples.query_request("123").arguments,
    })})
    with pytest.raises(ExtensionContractError):
        checks.apply(
            fixtures.request(), Drop(input_id="core-entry", reason="Hide"), Replace[ProjectionChange](
                input_id=original.change_id, document=replacement,
            ),
        )
