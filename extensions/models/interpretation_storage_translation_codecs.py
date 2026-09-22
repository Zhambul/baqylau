# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep extension translation requests, inputs, and decoder state by reference."""

from baqylau_extension_api.models.translation_inputs import (
    ExtensionTranslationRequest,
    TranslationInput,
    TranslationState,
)

from extensions.models.interpretation_bodies import (
    CONTENT_BUNDLE_KIND,
    ENCODED_DOCUMENT_KIND,
    RAW_INPUT_KIND,
    BodyResolver,
    BodyStore,
)
from extensions.models.interpretation_storage_context import _load_context, _store_context
from extensions.models.interpretation_storage_requests import (
    StoredExtensionTranslationRequest,
    StoredTranslationInput,
    StoredTranslationState,
)


def _store_translation_request(
    request: ExtensionTranslationRequest, store: BodyStore,
) -> StoredExtensionTranslationRequest:
    return StoredExtensionTranslationRequest(
        context=_store_context(request.context, store),
        inputs=tuple(_store_translation_input(source, store) for source in request.inputs),
        state=_store_translation_state(request.state, store),
        content_snapshot=store.intern(request.content_snapshot, CONTENT_BUNDLE_KIND),
    )


def _load_translation_request(
    request: StoredExtensionTranslationRequest, resolver: BodyResolver,
) -> ExtensionTranslationRequest:
    return ExtensionTranslationRequest(
        context=_load_context(request.context, resolver),
        inputs=tuple(_load_translation_input(source, resolver) for source in request.inputs),
        state=_load_translation_state(request.state, resolver),
        content_snapshot=resolver.resolve_content(request.content_snapshot),
    )


def _store_translation_input(source: TranslationInput, store: BodyStore) -> StoredTranslationInput:
    return StoredTranslationInput(
        source=store.intern(source.source, RAW_INPUT_KIND), schema_ref=source.schema_ref,
        occurred_at=source.occurred_at, causes=source.causes,
    )


def _load_translation_input(source: StoredTranslationInput, resolver: BodyResolver) -> TranslationInput:
    return TranslationInput(
        source=resolver.resolve_raw_input(source.source), schema_ref=source.schema_ref,
        occurred_at=source.occurred_at, causes=source.causes,
    )


def _store_translation_state(state: TranslationState, store: BodyStore) -> StoredTranslationState:
    return StoredTranslationState(
        revision=state.revision,
        document=None if state.document is None else store.intern(state.document, ENCODED_DOCUMENT_KIND),
    )


def _load_translation_state(state: StoredTranslationState, resolver: BodyResolver) -> TranslationState:
    return TranslationState(
        revision=state.revision,
        document=None if state.document is None else resolver.resolve_document(state.document),
    )
