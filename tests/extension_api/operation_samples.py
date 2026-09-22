# Copyright (c) 2026 Zhambyl Yermagambet
"""Build fixed query, command, and observation contract fixtures."""

from typing import Literal

from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.data import DocumentDefinition, ProcessingSelection
from baqylau_extension_api.manifest.metadata import BackendEntry
from baqylau_extension_api.manifest.operations import CommandDefinition, QueryDefinition
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models import observations, operations, queries
from baqylau_extension_api.models.commands import CommandBinding, CommandRequest
from baqylau_extension_api.models.documents import EncodedDocument, SchemaDefinition
from baqylau_extension_api.models.scopes import InstallationScope

from tests.extension_api import manifest_samples, samples

OWNER = "test.sample"
QUERY_ID = f"{OWNER}.read"
COMMAND_ID = f"{OWNER}.command"
SOURCE_TYPE = f"{OWNER}.observation"


def schema_definition() -> SchemaDefinition:
    """Own the text schema used by the small operation examples.

    Returns:
        The exact schema bytes with the fixture's owner identity.

    """
    schema = samples.schema_definition()
    reference = schema.reference.model_copy(update={"owner": OWNER})
    return schema.model_copy(update={"reference": reference})


def operation_binding(operation_id: str = QUERY_ID) -> operations.OperationBinding:
    """Bind a call to the fixture worker without creating a coding session.

    Returns:
        An installation-scoped call identity.

    """
    return operations.OperationBinding(
        extension_id=OWNER, operation_id=operation_id, scope=InstallationScope(),
        runtime_revision=samples.RUNTIME_REVISION, call_id="call-1",
    )


def query_request(encoded: str = '"input"') -> queries.QueryRequest:
    """Select a bounded live read with typed arguments.

    Returns:
        A complete query request using the fixture schema.

    """
    return queries.QueryRequest(
        binding=operation_binding(), settings_revision=0,
        arguments=EncodedDocument(schema_ref=schema_definition().reference, json_text=encoded),
    )


def command_request(encoded: str = '"complete"') -> CommandRequest:
    """Name one host-accepted command attempt and its stable request key.

    Returns:
        A read-class command request unless a test selects a write declaration.

    """
    return CommandRequest(
        binding=CommandBinding(
            extension_id=OWNER, operation_id=COMMAND_ID, scope=InstallationScope(),
            runtime_revision=samples.RUNTIME_REVISION, call_id="attempt-1", job_id="job-1", request_key="request-1",
        ), arguments=EncodedDocument(schema_ref=schema_definition().reference, json_text=encoded), settings_revision=0,
    )


def manifest(*, effect: Literal["read", "write"] = "read", reconciliation: bool = True) -> ExtensionManifest:
    """Declare the feature operations without importing their implementation.

    Returns:
        A complete data-only query, command, and raw-transform registration.

    """
    reference = schema_definition().reference
    return manifest_samples.backend_manifest(OWNER).model_copy(update={
        "backend": BackendEntry(module="operations_backend"),
        "capabilities": ("lifecycle", "raw_transformer", "queries", "commands"),
        "schemas": (schema_definition(),),
        "contributions": Contributions(
            queries=(QueryDefinition(name=QUERY_ID, scopes=("installation",), arguments=reference, result=reference),),
            commands=(CommandDefinition(
                name=COMMAND_ID, scopes=("installation",), arguments=reference, result=reference,
                effect=effect, reconciliation=reconciliation,
            ),),
            processing=(ProcessingSelection(
                capability="raw_transformer", scopes=("session",), input_types=("test.record",),
            ),),
        ),
    })


def observation() -> observations.ObservationCandidate:
    """Describe one new recorded input, not an already committed fact.

    Returns:
        A stable key inside a job-specific source identity.

    """
    return observations.ObservationCandidate(
        observation_key="result", source_identity="job:job-1", source_type=SOURCE_TYPE,
        scope=InstallationScope(), document=query_request().arguments,
    )


def source_manifest() -> ExtensionManifest:
    """Add a source declaration for pure observation validation tests.

    Returns:
        A declaration only; the operation worker does not implement its translator.

    """
    original = manifest()
    source = DocumentDefinition(name=SOURCE_TYPE, schema_ref=schema_definition().reference, scopes=("installation",))
    return original.model_copy(update={
        "capabilities": (*original.capabilities, "translator"),
        "contributions": original.contributions.model_copy(update={"source_types": (source,)}),
    })
