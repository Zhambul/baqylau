# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep ordered journal steps with typed references instead of repeated bodies."""

from extensions.models import interpretation_steps as steps
from extensions.models.interpretation_bodies import BodyResolver, BodyStore
from extensions.models.interpretation_storage_context import StoredProcessingContext as StoredProcessingContext
from extensions.models.interpretation_storage_core import (
    StoredCoreActivityStep as StoredCoreActivityStep,
    StoredCoreLifecycleStep as StoredCoreLifecycleStep,
    StoredCoreTranslationStep as StoredCoreTranslationStep,
    StoredTranslatedCoreInput as StoredTranslatedCoreInput,
)
from extensions.models.interpretation_storage_load_codecs import (
    _load_canonical_outcome,
    _load_canonical_request,
    _load_core_step,
    _load_raw_outcome,
    _load_raw_request,
    _load_translation_outcome,
    _load_translation_request,
)
from extensions.models.interpretation_storage_models import (
    StoredCanonicalTransformStep as StoredCanonicalTransformStep,
    StoredExtensionTranslationStep as StoredExtensionTranslationStep,
    StoredInterpretationStep as StoredInterpretationStep,
    StoredRawTransformStep as StoredRawTransformStep,
)
from extensions.models.interpretation_storage_operations import (
    StoredInsert as StoredInsert,
    StoredOperation as StoredOperation,
    StoredReplace as StoredReplace,
)
from extensions.models.interpretation_storage_outcomes import (
    StoredAppliedStep as StoredAppliedStep,
    StoredFailedStep as StoredFailedStep,
    StoredRawTransformResult as StoredRawTransformResult,
    StoredStepOutcome as StoredStepOutcome,
    StoredTransformResult as StoredTransformResult,
)
from extensions.models.interpretation_storage_requests import (
    StoredCanonicalTransformRequest as StoredCanonicalTransformRequest,
    StoredExtensionTranslationRequest as StoredExtensionTranslationRequest,
    StoredRawTransformRequest as StoredRawTransformRequest,
    StoredTranslationInput as StoredTranslationInput,
    StoredTranslationState as StoredTranslationState,
)
from extensions.models.interpretation_storage_results import (
    StoredExtensionTranslationResult as StoredExtensionTranslationResult,
    StoredTranslatedFact as StoredTranslatedFact,
    StoredTranslatedInput as StoredTranslatedInput,
    StoredTranslationDecision as StoredTranslationDecision,
)
from extensions.models.interpretation_storage_store_codecs import (
    _store_canonical_outcome,
    _store_canonical_request,
    _store_core_step,
    _store_raw_outcome,
    _store_raw_request,
    _store_translation_outcome,
    _store_translation_request,
)


def store_step(step: steps.InterpretationStep, store: BodyStore) -> StoredInterpretationStep:
    """Replace every body in one step with a typed reference.

    Returns:
        The stored step with shared bodies.

    Raises:
        TypeError: If the step has no stored form.

    """
    if isinstance(step, steps.RawTransformStep):
        return StoredRawTransformStep(
            request=_store_raw_request(step.request, store), outcome=_store_raw_outcome(step.outcome, store),
        )
    if isinstance(step, steps.ExtensionTranslationStep):
        return StoredExtensionTranslationStep(
            request=_store_translation_request(step.request, store),
            outcome=_store_translation_outcome(step.outcome, store),
        )
    if isinstance(step, steps.CanonicalTransformStep):
        return StoredCanonicalTransformStep(
            request=_store_canonical_request(step.request, store),
            outcome=_store_canonical_outcome(step.outcome, store),
        )
    core = _store_core_step(step, store)
    if core is not None:
        return core
    if isinstance(step, steps.UnavailableInputStep | steps.LimitStep):
        return step
    message = "interpretation step has no stored form"
    raise TypeError(message)


def load_step(stored: StoredInterpretationStep, resolver: BodyResolver) -> steps.InterpretationStep:
    """Restore every referenced body in one stored step.

    Returns:
        The complete logical step.

    """
    if isinstance(stored, StoredRawTransformStep):
        return steps.RawTransformStep(
            request=_load_raw_request(stored.request, resolver), outcome=_load_raw_outcome(stored.outcome, resolver),
        )
    if isinstance(stored, StoredExtensionTranslationStep):
        return steps.ExtensionTranslationStep(
            request=_load_translation_request(stored.request, resolver),
            outcome=_load_translation_outcome(stored.outcome, resolver),
        )
    if isinstance(stored, StoredCanonicalTransformStep):
        return steps.CanonicalTransformStep(
            request=_load_canonical_request(stored.request, resolver),
            outcome=_load_canonical_outcome(stored.outcome, resolver),
        )
    if isinstance(stored, StoredCoreTranslationStep | StoredCoreActivityStep | StoredCoreLifecycleStep):
        return _load_core_step(stored, resolver)
    return stored
