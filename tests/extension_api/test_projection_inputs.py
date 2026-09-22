# Copyright (c) 2026 Zhambyl Yermagambet
"""Check projector selection, record ownership, and captured schemas."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.projections import ProjectionRequest
from baqylau_extension_api.models.records import DeletedRecord, MissingRecord
from baqylau_extension_api.projection import inputs
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import operation_samples, projection_checks, projection_samples, samples, transform_samples


def test_projection_accepts_declared_input() -> None:
    """Validate the same selected facts before and after the host record read."""
    manifest = projection_samples.manifest()
    selected = projection_samples.selection_request()
    assert inputs.validate_projection_selection(manifest, SchemaSet(manifest.schemas), selected) == selected
    assert projection_checks.validate_request(projection_samples.request()) == projection_samples.request()


@pytest.mark.parametrize("change", [{"extension_id": "test.another"}, {"settings": samples.encoded_document()}])
def test_projection_checks_context_registration(change: dict[str, object]) -> None:
    """Reject a wrong package owner or settings that were not registered."""
    request = projection_samples.request()
    context = request.binding.context.model_copy(update=change)
    binding = request.binding.model_copy(update={"context": context})
    with pytest.raises(ExtensionContractError):
        projection_checks.validate_request(request.model_copy(update={"binding": binding}))


@pytest.mark.parametrize("change", [
    {"event_type": "test.sample.unknown"}, {"document": operation_samples.query_request("123").arguments},
])
def test_projection_rejects_unselected_input(change: dict[str, object]) -> None:
    """Reject an unselected type or an invalid captured document."""
    request = projection_samples.request()
    stored = request.events[0]
    stored = stored.model_copy(update={"fact": stored.fact.model_copy(update=change)})
    with pytest.raises(ExtensionContractError):
        projection_checks.validate_request(request.model_copy(update={"events": (stored,)}))


def test_projection_accepts_typed_core_input() -> None:
    """Read a closed core fact without changing its payload into an open document."""
    original = projection_samples.request()
    binding = original.binding.model_copy(update={
        "context": original.binding.context.model_copy(update={"scope": samples.SESSION}),
        "snapshot": original.binding.snapshot.model_copy(update={"scope": samples.SESSION}),
    })
    stored = original.events[0].model_copy(update={"fact": transform_samples.core_fact()})
    request = ProjectionRequest(binding=binding, events=(stored,))
    assert projection_checks.validate_request(request) == request


@pytest.mark.parametrize("change", [
    {"owner": "test.another", "collection": "test.another.records"}, {"collection": "test.sample.unknown"},
])
def test_projection_rejects_unowned_record(change: dict[str, object]) -> None:
    """Do not expose another owner's record as this projector's private state."""
    request = projection_samples.request()
    row = MissingRecord(key=projection_samples.record_key().model_copy(update=change))
    with pytest.raises(ExtensionContractError):
        projection_checks.validate_request(request.model_copy(update={"prior_records": (row,)}))


@pytest.mark.parametrize("deleted", [True, False])
def test_projection_rejects_wrong_prior_schema(*, deleted: bool) -> None:
    """Check deletion markers as well as live record documents."""
    request = projection_samples.request()
    row = projection_samples.stored_record()
    if deleted:
        marker = DeletedRecord(
            key=row.key, revision=row.revision, schema_ref=samples.encoded_document().schema_ref,
        )
        request = request.model_copy(update={"prior_records": (marker,)})
    else:
        request = request.model_copy(update={
            "prior_records": (row.model_copy(update={"document": samples.encoded_document()}),),
        })
    with pytest.raises(ExtensionContractError):
        projection_checks.validate_request(request)


def test_projection_requires_declared_capability() -> None:
    """Do not execute projection when no matching capability is present."""
    manifest = projection_samples.manifest().model_copy(update={"capabilities": ("lifecycle",)})
    with pytest.raises(ExtensionContractError, match="declared projector"):
        inputs.validate_projection_request(manifest, SchemaSet(manifest.schemas), projection_samples.request())
