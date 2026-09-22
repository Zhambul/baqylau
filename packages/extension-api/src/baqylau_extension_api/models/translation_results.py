# Copyright (c) 2026 Zhambyl Yermagambet
"""Record a separate translation decision for every supplied raw input."""

from typing import Annotated, Literal

from pydantic import Field

from baqylau_extension_api.models.base import NonemptyText, OpaqueId, Revision, WireModel
from baqylau_extension_api.models.canonical import CanonicalFact
from baqylau_extension_api.models.documents import Diagnostic, EncodedDocument
from baqylau_extension_api.models.events import ProcessingContext
from baqylau_extension_api.models.transform_requests import MAX_TRANSFORM_INPUTS
from baqylau_extension_api.models.transforms import MAX_TRANSFORM_OUTPUTS


class TranslatedFact(WireModel):
    """Name a logical fact independently of observation and processing revisions."""

    fact_key: OpaqueId
    fact: CanonicalFact


class TranslatedInput(WireModel):
    """Record one or more candidate facts in feature-selected order."""

    verdict: Literal["translated"] = "translated"
    input_id: OpaqueId
    facts: Annotated[tuple[TranslatedFact, ...], Field(min_length=1, max_length=MAX_TRANSFORM_OUTPUTS)]


class IgnoredInput(WireModel):
    """Record a known input that needs no canonical fact."""

    verdict: Literal["ignored"] = "ignored"
    input_id: OpaqueId
    reason: NonemptyText


class UnsupportedInput(WireModel):
    """Keep unknown input visible for audit and conformance checks."""

    verdict: Literal["unsupported"] = "unsupported"
    input_id: OpaqueId
    diagnostic: Diagnostic


class FailedInput(WireModel):
    """Record a known decoder failure instead of an intentional ignore."""

    verdict: Literal["failed"] = "failed"
    input_id: OpaqueId
    diagnostic: Diagnostic


TranslationDecision = Annotated[
    TranslatedInput | IgnoredInput | UnsupportedInput | FailedInput, Field(discriminator="verdict"),
]


class ExtensionTranslationResult(WireModel):
    """Propose complete decisions and next decoder state for atomic acceptance."""

    context: ProcessingContext
    state_revision: Revision
    decisions: Annotated[tuple[TranslationDecision, ...], Field(max_length=MAX_TRANSFORM_INPUTS)]
    next_state: EncodedDocument | None = None
