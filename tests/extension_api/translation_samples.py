# Copyright (c) 2026 Zhambyl Yermagambet
"""Build recorded translation input and stable candidate facts."""

from baqylau_extension_api.models.content import ContentBundle, encode_content
from baqylau_extension_api.models.events import ExtensionFact, RawInput, SourceReference
from baqylau_extension_api.models.translation_inputs import (
    ExtensionTranslationRequest,
    TranslationInput,
    TranslationState,
)
from baqylau_extension_api.models.translation_results import ExtensionTranslationResult, TranslatedFact, TranslatedInput
from baqylau_extension_api.translation.inputs import input_document
from baqylau_extension_api.translation_identity import TranslationIdentity, translated_event_id

from tests.extension_api import operation_samples, samples, source_samples


def request(encoded: bytes = b'"message-1"') -> ExtensionTranslationRequest:
    """Capture all bytes needed by a pure decoder.

    Returns:
        One extension input in installation scope with empty captured state.

    """
    context = samples.processing_context().model_copy(update={
        "extension_id": operation_samples.OWNER, "scope": source_samples.context().binding.scope,
    })
    blob = encode_content(encoded, "application/json")
    source = RawInput(
        input_id="raw-1", scope=context.scope, source_type=operation_samples.SOURCE_TYPE,
        source=SourceReference(
            raw_event_id="original-1", source_identity=source_samples.SOURCE_ID, source_position="1",
        ),
        content=blob.reference, origin="extension", owner=operation_samples.OWNER,
    )
    return ExtensionTranslationRequest(
        context=context, state=TranslationState(revision=0), content_snapshot=ContentBundle(blobs=(blob,)),
        inputs=(TranslationInput(source=source, schema_ref=operation_samples.schema_definition().reference),),
    )


def translated_fact(translation_request: ExtensionTranslationRequest | None = None) -> TranslatedFact:
    """Use an extension-owned logical key instead of the raw row ID.

    Returns:
        A complete candidate with a repeatable ID and declared event document.

    """
    selected = request() if translation_request is None else translation_request
    identity = TranslationIdentity(
        extension_id=operation_samples.OWNER, scope=selected.context.scope, fact_key="message-1",
    )
    return TranslatedFact(fact_key=identity.fact_key, fact=ExtensionFact(
        event_id=translated_event_id(identity), scope=selected.context.scope, event_type=source_samples.EVENT_TYPE,
        document=input_document(selected, selected.inputs[0]), causes=selected.inputs[0].causes,
    ))


def result(translation_request: ExtensionTranslationRequest | None = None) -> ExtensionTranslationResult:
    """Return one translated decision tied to the exact captured state.

    Returns:
        A complete one-input proposal with unchanged decoder state.

    """
    selected = request() if translation_request is None else translation_request
    facts = (translated_fact(selected),)
    decision = TranslatedInput(input_id=selected.inputs[0].source.input_id, facts=facts)
    return ExtensionTranslationResult(
        context=selected.context, state_revision=selected.state.revision, next_state=selected.state.document,
        decisions=(decision,),
    )


def session_request() -> ExtensionTranslationRequest:
    """Select an existing session actor for typed core output tests.

    Returns:
        A complete session-scoped extension source request.

    """
    selected = request()
    source = selected.inputs[0]
    raw = source.source.model_copy(update={"scope": samples.SESSION})
    source = source.model_copy(update={"source": raw})
    return selected.model_copy(update={
        "context": selected.context.model_copy(update={"scope": samples.SESSION}), "inputs": (source,),
    })
