# Copyright (c) 2026 Zhambyl Yermagambet
"""Capture peer-owned projection data with exact registered schemas."""

from baqylau_extension_api.models.documents import SchemaDefinition
from baqylau_extension_api.models.projection_changes import (
    ExtensionEntryChange,
    ExtensionRecordChange,
    ProjectionChange,
)
from baqylau_extension_api.models.projection_transforms import ProjectionTransformRequest, ProjectionTransformResult
from baqylau_extension_api.models.transforms import TransformOperation
from baqylau_extension_api.projection_transform.results import apply_projection_transform
from baqylau_extension_api.schemas import SchemaSet

from tests.extension_api import operation_samples, projection_transform_samples as fixtures

PEER = "test.peer"


def schema() -> SchemaDefinition:
    """Register the peer's schema without changing its document format.

    Returns:
        The peer-owned text schema.

    """
    original = operation_samples.schema_definition()
    return original.model_copy(update={"reference": original.reference.model_copy(update={
        "owner": PEER,
    })})


def entry() -> ExtensionEntryChange:
    """Supply a valid peer row to the current transform stage.

    Returns:
        A feed row whose identity belongs to the peer.

    """
    original = fixtures.extension_entry()
    document = original.entry.document.model_copy(update={"schema_ref": schema().reference})
    row = original.entry.model_copy(update={"entry_type": f"{PEER}.feed", "document": document})
    return original.model_copy(update={"owner": PEER, "entry": row})


def record() -> ExtensionRecordChange:
    """Supply a peer record proposal with the original captured revision.

    Returns:
        A peer-owned put proposal.

    """
    original = fixtures.record_change()
    write = original.write.model_copy(update={
        "key": original.write.key.model_copy(update={"owner": PEER, "collection": f"{PEER}.state"}),
        "document": operation_samples.query_request().arguments.model_copy(update={
            "schema_ref": schema().reference,
        }),
    })
    return original.model_copy(update={"write": write})


def request() -> ProjectionTransformRequest:
    """Keep the peer's captured row separate from the current package manifest.

    Returns:
        A valid transform input with a peer feed row and record proposal.

    """
    original = fixtures.request()
    captured = original.prior_records[0].model_copy(update={
        "key": record().write.key,
        "document": operation_samples.query_request().arguments.model_copy(update={
            "schema_ref": schema().reference,
        }),
    })
    return original.model_copy(update={
        "changes": (entry(), record()), "prior_records": (captured,),
    })


def apply(*operations: TransformOperation[ProjectionChange]) -> tuple[ProjectionChange, ...]:
    """Validate the result against both registered owners without changing storage.

    Returns:
        Fully checked peer changes.

    """
    manifest = fixtures.manifest()
    captured = request()
    response = ProjectionTransformResult(binding=captured.binding, operations=operations)
    return apply_projection_transform(manifest, captured, response, SchemaSet((*manifest.schemas, schema())))
