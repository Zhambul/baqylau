# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the declarations used by the external worker process tests."""

from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.data import ProcessingSelection
from baqylau_extension_api.manifest.metadata import BackendEntry
from baqylau_extension_api.models.content import ContentBundle, encode_content
from baqylau_extension_api.models.directory import DirectoryEntry, DirectoryRequest, DirectorySnapshot
from baqylau_extension_api.models.transforms import CanonicalTransformRequest, RawTransformRequest
from baqylau_extension_api.runtime import methods
from baqylau_extension_api.runtime.channel import RpcChannel
from baqylau_extension_api.runtime.codec import ModelHandler
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from pydantic import TypeAdapter

from tests.extension_api import manifest_samples, samples, transform_samples


def load_request() -> WorkerLoadRequest:
    """Declare the package copied outside the host repository for each test.

    Returns:
        A complete backend manifest and host-selected identity.

    """
    environment = samples.worker_environment()
    manifest = manifest_samples.backend_manifest(environment.extension_info.extension_id).model_copy(update={
        "backend": BackendEntry(module="sample_backend"),
        "capabilities": ("lifecycle", "raw_transformer", "canonical_transformer"),
        "contributions": Contributions(processing=(
            ProcessingSelection(capability="raw_transformer", scopes=("session",), input_types=("test.record",)),
            ProcessingSelection(
                capability="canonical_transformer", scopes=("session",), input_types=("session_title_changed",),
            ),
        )),
    })
    return WorkerLoadRequest(manifest=manifest, environment=environment)


def directory_snapshot(request: DirectoryRequest) -> DirectorySnapshot:
    """Supply the active peer that the external factory uses to select its handler.

    Returns:
        A fixed catalog response without importing a peer implementation.

    """
    assert request.active_only
    return DirectorySnapshot(
        catalog_revision=1, runtime_revision=samples.RUNTIME_REVISION,
        entries=(DirectoryEntry(extension_info=samples.extension_info(), state="enabled"),),
    )


def raw_request() -> RawTransformRequest:
    """Bind raw input to the sample worker, not to its optional peer.

    Returns:
        One exact immutable raw observation.

    """
    return RawTransformRequest(
        context=samples.processing_context().model_copy(update={"extension_id": "test.sample"}),
        inputs=(samples.raw_input(),),
        content_snapshot=ContentBundle(blobs=(encode_content(b"hello", "text/plain"),)),
    )


def register_directory(channel: RpcChannel) -> None:
    """Register a typed host service before any worker factory can call it."""
    channel.register(methods.DIRECTORY, ModelHandler(
        DirectoryRequest, TypeAdapter(DirectorySnapshot), directory_snapshot,
    ), "live")


def canonical_request(*, complete: bool = False) -> CanonicalTransformRequest:
    """Build a complete typed core event for the external worker.

    Returns:
        A canonical request owned by the sample extension.

    """
    request = transform_samples.canonical_request(transform_samples.core_fact())
    context = request.context.model_copy(update={"extension_id": "test.sample"})
    prior = request.prior_state.model_copy(update={"complete": complete})
    return request.model_copy(update={"context": context, "prior_state": prior})
