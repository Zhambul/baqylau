# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate candidate ownership, stable identity, origin links, and next state."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import lookup
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.canonical import CoreFact
from baqylau_extension_api.models.translation_inputs import ExtensionTranslationRequest, TranslationInput
from baqylau_extension_api.models.translation_results import ExtensionTranslationResult, TranslatedFact, TranslatedInput
from baqylau_extension_api.operations import documents
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.translation_identity import TranslationIdentity, translated_event_id


def validate_translation_documents(
    manifest: ExtensionManifest, schemas: SchemaSet,
    request: ExtensionTranslationRequest, response: ExtensionTranslationResult,
) -> None:
    """Check every proposal before the host uses any facts or next decoder state."""
    if response.next_state is not None:
        documents.validate_owned_document(response.next_state, manifest.extension_id, schemas)
    for source, decision in zip(request.inputs, response.decisions, strict=True):
        if isinstance(decision, TranslatedInput):
            for output in decision.facts:
                _validate_fact(manifest, schemas, source, output)


def _validate_fact(
    manifest: ExtensionManifest, schemas: SchemaSet, source: TranslationInput, output: TranslatedFact,
) -> None:
    identity = TranslationIdentity(
        extension_id=manifest.extension_id, scope=source.source.scope, fact_key=output.fact_key,
    )
    expected_id = translated_event_id(identity)
    if output.fact.scope != source.source.scope or output.fact.event_id != expected_id:
        message = "translated fact changed its scope or stable logical identity"
        raise ExtensionContractError(message)
    if isinstance(output.fact, CoreFact):
        _validate_core_source(source, output.fact)
    else:
        definition = lookup.event_definition(manifest, output.fact.event_type, source.source.scope.kind)
        documents.require_document_schema(output.fact.document, definition.schema_ref, schemas, "translated event")
        documents.validate_owned_document(output.fact.document, manifest.extension_id, schemas)
        if not set(source.causes) <= set(output.fact.causes):
            message = "translated fact must retain its observation's cause references"
            raise ExtensionContractError(message)


def _validate_core_source(source: TranslationInput, fact: CoreFact) -> None:
    if fact.raw_event_ids != (source.source.source.raw_event_id,):
        message = "translated core fact must retain its original raw source reference"
        raise ExtensionContractError(message)
