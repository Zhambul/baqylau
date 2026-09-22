# Copyright (c) 2026 Zhambyl Yermagambet
"""Build source declarations and complete read proposals for contract tests."""

from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.data import DocumentDefinition, ScopeKinds
from baqylau_extension_api.manifest.metadata import BackendEntry
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.manifest.settings import SettingsDefinition
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.source_results import PositionedObservation, SourceBatch, SourceReadResult
from baqylau_extension_api.models.sources import SourceBinding, SourceContext, SourceDescriptor, SourceReadRequest
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.sources import batches

from tests.extension_api import manifest_samples, operation_samples, samples

SCOPES: ScopeKinds = ("installation", "repository", "session")
EVENT_TYPE = "test.sample.fact"
SOURCE_ID = "journal-1"


def manifest(settings: EncodedDocument | None = None) -> ExtensionManifest:
    """Declare sources and a translator without loading the backend.

    Returns:
        Owned source, event, and optional settings schemas.

    """
    reference = operation_samples.schema_definition().reference
    return manifest_samples.backend_manifest(operation_samples.OWNER).model_copy(update={
        "backend": BackendEntry(module="source_backend"),
        "capabilities": ("lifecycle", "sources", "translator"),
        "schemas": (operation_samples.schema_definition(),),
        "settings": None if settings is None else SettingsDefinition(defaults=settings, scopes=SCOPES),
        "contributions": Contributions(
            source_types=(DocumentDefinition(name=operation_samples.SOURCE_TYPE, schema_ref=reference, scopes=SCOPES),),
            event_types=(DocumentDefinition(name=EVENT_TYPE, schema_ref=reference, scopes=SCOPES),),
        ),
    })


def context() -> SourceContext:
    """Select an installation source without creating a coding session.

    Returns:
        The immutable fixture source context.

    """
    return SourceContext(binding=SourceBinding(
        extension_id=operation_samples.OWNER, scope=operation_samples.operation_binding().scope,
        runtime_revision=samples.RUNTIME_REVISION, call_id="source-call-1",
    ), settings_revision=0)


def descriptor() -> SourceDescriptor:
    """Describe a fixture source with one event-driven file watch.

    Returns:
        A stable source identity and its declared document type.

    """
    return SourceDescriptor(
        source_identity=SOURCE_ID, source_type=operation_samples.SOURCE_TYPE,
        watch_paths=("/workspace/extension-fixture.jsonl",),
    )


def read_request() -> SourceReadRequest:
    """Read from the start before the host has committed a position.

    Returns:
        A complete bounded source read request.

    """
    return SourceReadRequest(context=context(), source=descriptor())


def batch() -> SourceBatch:
    """Propose one original observation and its atomic resume position.

    Returns:
        A complete source batch with a stable observation key.

    """
    candidate = operation_samples.observation().model_copy(update={"source_identity": SOURCE_ID})
    return SourceBatch(
        binding=batches.source_read_binding(read_request()), next_position="line:1",
        observations=(PositionedObservation(position="line:1", observation=candidate),),
    )


def validate_batch(request: SourceReadRequest, response: SourceReadResult) -> SourceReadResult:
    """Run both boundary and declaration checks in test assertions.

    Returns:
        A complete checked batch or read failure.

    """
    declaration = manifest()
    checked = batches.validate_source_batch(request, response)
    batches.validate_batch_documents(declaration, SchemaSet(declaration.schemas), checked)
    return checked


def first_document(response: SourceBatch) -> str:
    """Read the original text from a nonempty test batch.

    Returns:
        The first captured document's exact JSON text.

    """
    observation = response.observations[0].observation
    return observation.document.json_text
