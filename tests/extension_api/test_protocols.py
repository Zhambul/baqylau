# Copyright (c) 2026 Zhambyl Yermagambet
"""Exercise explicit extension protocols and the peer directory service."""

from dataclasses import FrozenInstanceError, dataclass

import pytest
from baqylau_extension_api.contracts.services import ExtensionDirectory, ExtensionHostServices
from baqylau_extension_api.models.content import ContentBundle, encode_content
from baqylau_extension_api.models.directory import DirectoryEntry, DirectoryRequest, DirectorySnapshot
from baqylau_extension_api.models.lifecycle import ActivationReady, ActivationRequest, DeactivationRequest
from baqylau_extension_api.models.raw_transforms import RawTransformResult
from baqylau_extension_api.models.transforms import Drop, RawTransformRequest

from tests.extension_api import example, samples


@dataclass(frozen=True)
class SampleDirectory(ExtensionDirectory):
    """Return a pinned peer catalog for a worker factory."""

    entries: tuple[DirectoryEntry, ...] = ()

    def list_extensions(self, request: DirectoryRequest) -> DirectorySnapshot:
        """Read active peers or the full fixture catalog.

        Returns:
            A fixed metadata snapshot with no implementation handles.

        """
        entries = tuple(
            peer for peer in self.entries
            if not request.active_only or peer.state == "enabled"
        )
        return DirectorySnapshot(
            catalog_revision=1, runtime_revision=samples.RUNTIME_REVISION, entries=entries,
        )


def test_lifecycle_uses_the_requested_revision() -> None:
    """Prepare and release a worker through the public protocol only."""
    plugin = example.FACTORY(ExtensionHostServices(
        directory=SampleDirectory(), environment=samples.worker_environment(),
    ))
    lifecycle = plugin.capabilities.lifecycle
    active = lifecycle.activate(ActivationRequest(runtime_revision=samples.RUNTIME_REVISION, settings_revision=0))
    stopped = lifecycle.deactivate(DeactivationRequest(runtime_revision=samples.RUNTIME_REVISION, reason="disable"))
    assert isinstance(active, ActivationReady) and active.runtime_revision == samples.RUNTIME_REVISION
    assert stopped.runtime_revision == samples.RUNTIME_REVISION and not stopped.pending_job_ids


def test_capabilities_are_frozen_and_optional() -> None:
    """Represent an absent transform as None, not as an empty handler."""
    plugin = example.FACTORY(ExtensionHostServices(
        directory=SampleDirectory(), environment=samples.worker_environment(),
    ))
    assert plugin.capabilities.raw_transformer is None
    with pytest.raises(FrozenInstanceError):
        # Deliberately test a forbidden runtime write as well as the type rule.
        plugin.capabilities.raw_transformer = example.SampleTransformer()  # type: ignore[misc]


@pytest.mark.parametrize("state", ["enabled", "disabled"])
def test_factory_detects_active_peer(state: str) -> None:
    """Enable cooperation from metadata without importing the other package."""
    peer = DirectoryEntry.model_validate({"extension_info": samples.extension_info(), "state": state})
    plugin = example.FACTORY(ExtensionHostServices(
        directory=SampleDirectory(entries=(peer,)), environment=samples.worker_environment(),
    ))
    assert (plugin.capabilities.raw_transformer is not None) == (state == "enabled")


def test_transform_returns_typed_drop_operations() -> None:
    """Round-trip a plugin result while retaining the original input batch."""
    transformer = example.SampleTransformer()
    request = RawTransformRequest(
        context=samples.processing_context(), inputs=(samples.raw_input(),),
        content_snapshot=ContentBundle(blobs=(encode_content(b"hello", "text/plain"),)),
    )
    response = transformer.transform(RawTransformRequest.model_validate_json(request.model_dump_json()))
    decoded = RawTransformResult.model_validate_json(response.model_dump_json())
    assert decoded.operations == (Drop(input_id="raw-1", reason="sample suppression"),)
    assert request.inputs == (samples.raw_input(),)
