# Copyright (c) 2026 Zhambyl Yermagambet
"""Preserve committed source progress for empty, partial, and repeated reads."""

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.source_results import SourceBatch
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.sources import batches

from tests.extension_api import samples, source_samples


def test_source_batch_has_atomic_checkpoint() -> None:
    """Keep one original observation tied to its final source position."""
    manifest = source_samples.manifest()
    response = source_samples.batch()
    assert batches.validate_source_batch(source_samples.read_request(), response) == response
    batches.validate_batch_documents(manifest, SchemaSet(manifest.schemas), response)


@pytest.mark.parametrize("position", [None, "empty-checkpoint"])
def test_empty_batch_can_keep_or_advance(position: str | None) -> None:
    """Permit an idle read or explicit empty checkpoint without inventing input."""
    request = source_samples.read_request()
    response = SourceBatch(binding=batches.source_read_binding(request), next_position=position)
    assert batches.validate_source_batch(request, response) == response


@pytest.mark.parametrize("change", [
    {"next_position": None}, {"next_position": "past-final"},
])
def test_nonempty_batch_must_keep_final_position(change: dict[str, object]) -> None:
    """Do not lose or skip observations through an inconsistent checkpoint."""
    with pytest.raises(ExtensionContractError):
        batches.validate_source_batch(source_samples.read_request(), source_samples.batch().model_copy(update=change))


def test_more_work_requires_checkpoint_progress() -> None:
    """Reject a source that would request an immediate read with no progress."""
    request = source_samples.read_request()
    response = SourceBatch(binding=batches.source_read_binding(request), next_position=None, has_more=True)
    with pytest.raises(ExtensionContractError, match="must advance"):
        batches.validate_source_batch(request, response)


@pytest.mark.parametrize("position", [None, "committed"])
def test_committed_position_is_not_lost(position: str | None) -> None:
    """Retain an existing checkpoint when no new input is ready."""
    request = source_samples.read_request().model_copy(update={"after_position": "committed"})
    response = SourceBatch(binding=batches.source_read_binding(request), next_position=position)
    if position is None:
        with pytest.raises(ExtensionContractError, match="erase committed progress"):
            batches.validate_source_batch(request, response)
    else:
        assert batches.validate_source_batch(request, response) == response


@pytest.mark.parametrize("change", [
    {"source_identity": "another"}, {"source_type": "peer.raw"}, {"after_position": "other-position"},
])
def test_batch_repeats_exact_source_selection(change: dict[str, object]) -> None:
    """Do not accept a valid batch for a different selected source or checkpoint."""
    response = source_samples.batch()
    response = response.model_copy(update={"binding": response.binding.model_copy(update=change)})
    with pytest.raises(ExtensionContractError, match="selected source and position"):
        batches.validate_source_batch(source_samples.read_request(), response)


@pytest.mark.parametrize("change", [{"source_identity": "another"}, {"scope": samples.SESSION}])
def test_source_observation_cannot_change_scope(change: dict[str, object]) -> None:
    """Check both source identity and declared scope on every observation."""
    response = source_samples.batch()
    original = response.observations[0]
    changed = original.model_copy(update={"observation": original.observation.model_copy(update=change)})
    response = response.model_copy(update={"observations": (changed,)})
    with pytest.raises(ExtensionContractError):
        source_samples.validate_batch(source_samples.read_request(), response)
