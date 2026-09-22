# Copyright (c) 2026 Zhambyl Yermagambet
"""Supply immutable extension input and captured decoder state."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.models.base import OpaqueId, Revision, WireModel
from baqylau_extension_api.models.content import ContentBundle
from baqylau_extension_api.models.documents import EncodedDocument, SchemaRef
from baqylau_extension_api.models.events import MAX_CAUSE_COUNT, ProcessingContext, RawInput
from baqylau_extension_api.models.transform_requests import MAX_TRANSFORM_INPUTS


class TranslationInput(WireModel):
    """Attach stored document metadata to a raw translation input.

    Raw transforms can change content. Source metadata remains linked to the
    original observation named by the raw source reference.
    """

    source: RawInput
    schema_ref: SchemaRef
    occurred_at: float | None = None
    causes: Annotated[tuple[OpaqueId, ...], Field(max_length=MAX_CAUSE_COUNT)] = ()


class TranslationState(WireModel):
    """Read one host-owned decoder state revision without a mutable handle."""

    revision: Revision
    document: EncodedDocument | None = None


class ExtensionTranslationRequest(WireModel):
    """Translate one ordered scope batch from recorded bytes and state only."""

    context: ProcessingContext
    inputs: Annotated[tuple[TranslationInput, ...], Field(max_length=MAX_TRANSFORM_INPUTS)]
    state: TranslationState
    content_snapshot: ContentBundle = ContentBundle()
