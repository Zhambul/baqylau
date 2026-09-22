# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep original lifecycle, selected activity, and old complete core passes distinct."""

from typing import Annotated, Literal

from baqylau_extension_api.models import canonical, content, events, transforms
from baqylau_extension_api.models.base import Digest, NonemptyText, Revision, WireModel
from pydantic import Field

from domain.records import RecordedTranslationDecision


class TranslatedCoreInput(WireModel):
    """Keep checked core output with its exact selected activity bytes."""

    source: events.RawInput
    content_snapshot: content.ContentBundle
    translator_version: NonemptyText
    decision: RecordedTranslationDecision
    reason: NonemptyText | None = None
    facts: Annotated[tuple[canonical.CoreFact, ...], Field(max_length=transforms.MAX_TRANSFORM_OUTPUTS)] = ()


class CoreTranslationStep(TranslatedCoreInput):
    """Read an old complete core pass without treating it as a current activity pass."""

    stage: Literal["core_translation"] = "core_translation"


class CoreActivityStep(TranslatedCoreInput):
    """Record only activity from a surviving or added raw input."""

    stage: Literal["core_activity"] = "core_activity"


class CoreLifecycleStep(WireModel):
    """Bind required state to stored original bytes, including input too large for a worker."""

    stage: Literal["core_lifecycle"] = "core_lifecycle"
    content_byte_length: Revision
    content_digest: Digest
    translator_version: NonemptyText
    decision: RecordedTranslationDecision
    reason: NonemptyText | None = None
    facts: Annotated[tuple[canonical.CoreFact, ...], Field(max_length=transforms.MAX_TRANSFORM_OUTPUTS)] = ()
