# Copyright (c) 2026 Zhambyl Yermagambet
"""Check recorded source input, settings, and decoder state before translation."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import lookup, rules
from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.content import decode_base64
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.translation_inputs import ExtensionTranslationRequest, TranslationInput
from baqylau_extension_api.operations import documents
from baqylau_extension_api.schemas import SchemaSet


def validate_translation_request(
    manifest: ExtensionManifest, schemas: SchemaSet, request: ExtensionTranslationRequest,
) -> ExtensionTranslationRequest:
    """Check complete immutable input before calling a pure decoder.

    Returns:
        A checked request with owned state and declared source documents.

    Raises:
        ExtensionContractError: If the selected package has no matching translator.

    """
    checked = ExtensionTranslationRequest.model_validate(request)
    if checked.context.extension_id != manifest.extension_id or "translator" not in manifest.capabilities:
        message = "translation context does not match a declared translator"
        raise ExtensionContractError(message)
    rules.require_unique((source.source.input_id for source in checked.inputs), "translation input identities")
    documents.validate_settings(manifest, checked.context.settings, schemas)
    if checked.state.document is not None:
        documents.validate_owned_document(checked.state.document, manifest.extension_id, schemas)
    for source in checked.inputs:
        _validate_input(manifest, schemas, checked, source)
    return checked


def input_document(request: ExtensionTranslationRequest, source: TranslationInput) -> EncodedDocument:
    """Read the exact supplied bytes through the declared document codec.

    Returns:
        A document using stored schema metadata, not worker-selected metadata.

    Raises:
        ExtensionContractError: If the source content is not valid UTF-8.

    """
    content = request.content_snapshot.resolve(source.source.content)
    try:
        encoded = decode_base64(content.base64_text).decode("utf-8")
    except UnicodeDecodeError as error:
        message = "extension source content is not UTF-8"
        raise ExtensionContractError(message) from error
    return EncodedDocument(schema_ref=source.schema_ref, json_text=encoded)


def _validate_input(
    manifest: ExtensionManifest, schemas: SchemaSet, request: ExtensionTranslationRequest, source: TranslationInput,
) -> None:
    raw = source.source
    foreign_origin = raw.origin != "extension" or raw.owner != manifest.extension_id
    if foreign_origin or raw.scope != request.context.scope:
        message = "translation input must keep its declared extension origin and scope"
        raise ExtensionContractError(message)
    definition = lookup.source_definition(manifest, raw.source_type, raw.scope.kind)
    rules.require_unique(source.causes, "translation cause identities")
    documents.require_document_schema(
        input_document(request, source), definition.schema_ref, schemas, "source document",
    )
