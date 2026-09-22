# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep feature-owned record selection separate from host snapshot reads."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.projections import ProjectionReadSet
from baqylau_extension_api.models.records import MissingRecord
from baqylau_extension_api.projection import selection
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import projection_samples, samples


def test_capture_covers_selected_record_keys() -> None:
    """Require every selected row once, including explicit proof of absence."""
    request = projection_samples.selection_request()
    selected = ProjectionReadSet(binding=request.binding, keys=(projection_samples.record_key(),))
    captured = selection.capture_projection_request(request, selected, (projection_samples.stored_record(),))
    assert captured == projection_samples.request()
    missing = MissingRecord(key=selected.keys[0])
    assert selection.capture_projection_request(request, selected, (missing,)).prior_records == (missing,)


@pytest.mark.parametrize("change", [{"scope": samples.SESSION}, {
    "owner": "test.another", "collection": "test.another.records",
}])
def test_selection_rejects_foreign_record_keys(change: dict[str, object]) -> None:
    """Do not read another owner or scope through a pure projector."""
    request = projection_samples.selection_request()
    selected = ProjectionReadSet(
        binding=request.binding, keys=(projection_samples.record_key().model_copy(update=change),),
    )
    with pytest.raises(ExtensionContractError, match="context owner and scope"):
        selection.validate_read_set(request, selected)


def test_selection_rejects_duplicate_record_keys() -> None:
    """Do not accept the same selected key more than once."""
    request = projection_samples.selection_request()
    selected = ProjectionReadSet(binding=request.binding, keys=(projection_samples.record_key(),) * 2)
    with pytest.raises(ExtensionContractError, match="keys must be unique"):
        selection.validate_read_set(request, selected)


def test_selection_rejects_stale_binding() -> None:
    """Keep the record read tied to the exact selected snapshot."""
    request = projection_samples.selection_request()
    selected = ProjectionReadSet(binding=request.binding.model_copy(update={"after_input_cursor": 9}))
    with pytest.raises(ExtensionContractError, match="exact processing and snapshot"):
        selection.validate_read_set(request, selected)


@pytest.mark.parametrize("collection", [projection_samples.COLLECTION, "test.sample.unknown"])
def test_selection_requires_declared_collection(collection: str) -> None:
    """Check collection declarations before a host read can start."""
    request = projection_samples.selection_request()
    selected = ProjectionReadSet(binding=request.binding, keys=(
        projection_samples.record_key().model_copy(update={"collection": collection}),
    ))
    manifest = projection_samples.manifest()
    if collection == projection_samples.COLLECTION:
        selection.validate_read_registrations(manifest, SchemaSet(manifest.schemas), selected)
    else:
        with pytest.raises(ExtensionContractError, match="not declared"):
            selection.validate_read_registrations(manifest, SchemaSet(manifest.schemas), selected)


@pytest.mark.parametrize("absent", [True, False])
def test_capture_rejects_incomplete_key_coverage(*, absent: bool) -> None:
    """Do not interpret an omitted row as proof that its key is absent."""
    request = projection_samples.selection_request()
    selected = ProjectionReadSet(binding=request.binding, keys=(projection_samples.record_key(),))
    wrong = MissingRecord(key=selected.keys[0].model_copy(update={
        "key": "wrong",
    }))
    captured = () if absent else (wrong,)
    with pytest.raises(ExtensionContractError, match="exact selected keys"):
        selection.capture_projection_request(request, selected, captured)


def test_capture_rejects_row_after_snapshot() -> None:
    """Recheck row revisions when the host constructs the final request."""
    request = projection_samples.selection_request()
    selected = ProjectionReadSet(binding=request.binding, keys=(projection_samples.record_key(),))
    captured = (projection_samples.stored_record().model_copy(update={"revision": 8}),)
    with pytest.raises(ExtensionContractError, match="exceeds the projection snapshot"):
        selection.capture_projection_request(request, selected, captured)
