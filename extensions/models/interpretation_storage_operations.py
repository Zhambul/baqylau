# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep transform operations with referenced documents."""

from typing import Annotated, Literal

from baqylau_extension_api.models import base, canonical, events, transforms
from pydantic import Field

from extensions.models.interpretation_bodies import (
    CANONICAL_FACT_KIND,
    RAW_INPUT_KIND,
    BodyRef,
    BodyResolver,
    BodyStore,
)


class StoredReplace(base.WireModel):
    """Replace input data with a referenced document."""

    kind: Literal["replace"] = "replace"
    input_id: base.OpaqueId
    document: BodyRef


class StoredInsert(base.WireModel):
    """Add one referenced document relative to a named input."""

    kind: Literal["insert"] = "insert"
    input_id: base.OpaqueId
    output_key: base.Identifier
    position: Literal["before", "after"]
    document: BodyRef


type StoredOperation = Annotated[
    transforms.Keep | transforms.Drop | StoredReplace | StoredInsert,
    Field(discriminator="kind"),
]


def _store_raw_operation(
    operation: transforms.TransformOperation[events.RawInput], store: BodyStore,
) -> StoredOperation:
    if isinstance(operation, transforms.Keep | transforms.Drop):
        return operation
    document = store.intern(operation.document, RAW_INPUT_KIND)
    if isinstance(operation, transforms.Replace):
        return StoredReplace(input_id=operation.input_id, document=document)
    return StoredInsert(
        input_id=operation.input_id, output_key=operation.output_key, position=operation.position, document=document,
    )


def _load_raw_operation(
    operation: StoredOperation, resolver: BodyResolver,
) -> transforms.TransformOperation[events.RawInput]:
    if isinstance(operation, transforms.Keep | transforms.Drop):
        return operation
    document = resolver.resolve_raw_input(operation.document)
    if isinstance(operation, StoredReplace):
        return transforms.Replace(input_id=operation.input_id, document=document)
    return transforms.Insert(
        input_id=operation.input_id, output_key=operation.output_key, position=operation.position, document=document,
    )


def _store_canonical_operation(
    operation: transforms.TransformOperation[canonical.CanonicalFact], store: BodyStore,
) -> StoredOperation:
    if isinstance(operation, transforms.Keep | transforms.Drop):
        return operation
    document = store.intern(operation.document, CANONICAL_FACT_KIND)
    if isinstance(operation, transforms.Replace):
        return StoredReplace(input_id=operation.input_id, document=document)
    return StoredInsert(
        input_id=operation.input_id, output_key=operation.output_key, position=operation.position, document=document,
    )


def _load_canonical_operation(
    operation: StoredOperation, resolver: BodyResolver,
) -> transforms.TransformOperation[canonical.CanonicalFact]:
    if isinstance(operation, transforms.Keep | transforms.Drop):
        return operation
    document = resolver.resolve_fact(operation.document)
    if isinstance(operation, StoredReplace):
        return transforms.Replace(input_id=operation.input_id, document=document)
    return transforms.Insert(
        input_id=operation.input_id, output_key=operation.output_key, position=operation.position, document=document,
    )
