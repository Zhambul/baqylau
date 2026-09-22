# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep transform requests with referenced inputs, state, and prior boundary."""

from baqylau_extension_api.models import base, documents, transforms

from extensions.models.interpretation_bodies import (
    CONTENT_BUNDLE_KIND,
    RAW_INPUT_KIND,
    BodyRef,
    BodyResolver,
    BodyStore,
)
from extensions.models.interpretation_storage_context import (
    StoredProcessingContext,
    _load_context,
    _store_context,
)


class StoredRawTransformRequest(base.WireModel):
    """Name the raw inputs and their content snapshot by reference."""

    context: StoredProcessingContext
    inputs: tuple[BodyRef, ...]
    content_snapshot: BodyRef


class StoredTranslationInput(base.WireModel):
    """Attach stored document metadata to a referenced raw input."""

    source: BodyRef
    schema_ref: documents.SchemaRef
    occurred_at: float | None = None
    causes: tuple[base.OpaqueId, ...] = ()


class StoredTranslationState(base.WireModel):
    """Read one host-owned decoder state revision with a referenced document."""

    revision: base.Revision
    document: BodyRef | None = None


class StoredExtensionTranslationRequest(base.WireModel):
    """Translate one ordered scope batch from referenced bytes and state."""

    context: StoredProcessingContext
    inputs: tuple[StoredTranslationInput, ...]
    state: StoredTranslationState
    content_snapshot: BodyRef


class StoredCanonicalTransformRequest(base.WireModel):
    """Supply referenced candidates with their referenced prior boundary."""

    context: StoredProcessingContext
    inputs: tuple[BodyRef, ...]
    prior_state: BodyRef


def _store_raw_request(request: transforms.RawTransformRequest, store: BodyStore) -> StoredRawTransformRequest:
    return StoredRawTransformRequest(
        context=_store_context(request.context, store),
        inputs=tuple(store.intern(source, RAW_INPUT_KIND) for source in request.inputs),
        content_snapshot=store.intern(request.content_snapshot, CONTENT_BUNDLE_KIND),
    )


def _load_raw_request(request: StoredRawTransformRequest, resolver: BodyResolver) -> transforms.RawTransformRequest:
    return transforms.RawTransformRequest(
        context=_load_context(request.context, resolver),
        inputs=tuple(resolver.resolve_raw_input(ref) for ref in request.inputs),
        content_snapshot=resolver.resolve_content(request.content_snapshot),
    )
