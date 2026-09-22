# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep every ordered journal step with referenced requests and replies."""

from typing import Annotated, Literal

from baqylau_extension_api.models import base
from pydantic import Field

from extensions.models import interpretation_steps as steps
from extensions.models.interpretation_storage_core import (
    StoredCoreActivityStep,
    StoredCoreLifecycleStep,
    StoredCoreTranslationStep,
)
from extensions.models.interpretation_storage_outcomes import (
    StoredRawTransformResult,
    StoredStepOutcome,
    StoredTransformResult,
)
from extensions.models.interpretation_storage_requests import (
    StoredCanonicalTransformRequest,
    StoredExtensionTranslationRequest,
    StoredRawTransformRequest,
)
from extensions.models.interpretation_storage_results import StoredExtensionTranslationResult


class StoredRawTransformStep(base.WireModel):
    """Record one pure raw transform with referenced inputs and replies."""

    stage: Literal["raw"] = "raw"
    request: StoredRawTransformRequest
    outcome: StoredStepOutcome[StoredRawTransformResult]


class StoredExtensionTranslationStep(base.WireModel):
    """Retain source metadata, referenced bytes, decoder state, and complete decisions."""

    stage: Literal["extension_translation"] = "extension_translation"
    request: StoredExtensionTranslationRequest
    outcome: StoredStepOutcome[StoredExtensionTranslationResult]


class StoredCanonicalTransformStep(base.WireModel):
    """Retain referenced candidate input, prior state, and transform reply."""

    stage: Literal["canonical"] = "canonical"
    request: StoredCanonicalTransformRequest
    outcome: StoredStepOutcome[StoredTransformResult]


type StoredInterpretationStep = Annotated[
    StoredRawTransformStep | StoredExtensionTranslationStep | StoredCoreTranslationStep | StoredCoreActivityStep
    | StoredCoreLifecycleStep | StoredCanonicalTransformStep | steps.UnavailableInputStep | steps.LimitStep,
    Field(discriminator="stage"),
]
