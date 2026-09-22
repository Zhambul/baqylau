# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain typed processing inputs and replies in their original step order."""

from typing import Annotated, Literal

from baqylau_extension_api.models import documents, raw_transforms, transforms
from baqylau_extension_api.models.base import Digest, Revision, WireModel
from baqylau_extension_api.models.translation_inputs import ExtensionTranslationRequest
from baqylau_extension_api.models.translation_results import ExtensionTranslationResult
from pydantic import Field

from extensions.models.interpretation_control_steps import (
    LimitedCallStage as LimitedCallStage,
    LimitStep as LimitStep,
    UnavailableInputReason as UnavailableInputReason,
    UnavailableInputStep as UnavailableInputStep,
)
from extensions.models.interpretation_core_steps import (
    CoreActivityStep as CoreActivityStep,
    CoreLifecycleStep as CoreLifecycleStep,
    CoreTranslationStep as CoreTranslationStep,
)


class AppliedStep[Reply](WireModel):
    """Retain a reply that was checked and applied by the host."""

    kind: Literal["applied"] = "applied"
    reply: Reply


class FailedStep[Reply](WireModel):
    """Keep the input unchanged and retain a bounded failure and any typed proposal."""

    kind: Literal["failed"] = "failed"
    diagnostic: documents.Diagnostic
    reply: Reply | None = None


class RejectedStep(WireModel):
    """Keep the input unchanged and record a reply that journal admission rejected."""

    kind: Literal["rejected"] = "rejected"
    diagnostic: documents.Diagnostic
    observed_byte_length: Revision
    observed_digest: Digest


type StepOutcome[Reply] = Annotated[
    AppliedStep[Reply] | FailedStep[Reply] | RejectedStep,
    Field(discriminator="kind"),
]


class RawTransformStep(WireModel):
    """Record one pure raw transform without changing the original observation."""

    stage: Literal["raw"] = "raw"
    request: transforms.RawTransformRequest
    outcome: StepOutcome[raw_transforms.RawTransformResult]


class ExtensionTranslationStep(WireModel):
    """Retain source metadata, exact derived bytes, decoder state, and complete decisions."""

    stage: Literal["extension_translation"] = "extension_translation"
    request: ExtensionTranslationRequest
    outcome: StepOutcome[ExtensionTranslationResult]


class CanonicalTransformStep(WireModel):
    """Retain the complete candidate input, prior state, and transform reply."""

    stage: Literal["canonical"] = "canonical"
    request: transforms.CanonicalTransformRequest
    outcome: StepOutcome[transforms.CanonicalTransformResult]


InterpretationStep = Annotated[
    RawTransformStep | ExtensionTranslationStep | CoreTranslationStep | CoreActivityStep | CoreLifecycleStep
    | CanonicalTransformStep | UnavailableInputStep | LimitStep,
    Field(discriminator="stage"),
]
