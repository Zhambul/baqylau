# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep translation and canonical outcomes with referenced replies."""

from baqylau_extension_api.models import transforms
from baqylau_extension_api.models.translation_results import ExtensionTranslationResult

from extensions.models import interpretation_steps as steps
from extensions.models.interpretation_bodies import BodyResolver, BodyStore
from extensions.models.interpretation_storage_outcomes import (
    StoredAppliedStep,
    StoredFailedStep,
    StoredStepOutcome,
    StoredTransformResult,
    _required_reply,
)
from extensions.models.interpretation_storage_result_codecs import (
    _load_canonical_result,
    _load_translation_result,
    _store_canonical_result,
    _store_translation_result,
)
from extensions.models.interpretation_storage_results import StoredExtensionTranslationResult


def _store_translation_outcome(
    outcome: steps.StepOutcome[ExtensionTranslationResult], store: BodyStore,
) -> StoredStepOutcome[StoredExtensionTranslationResult]:
    if isinstance(outcome, steps.RejectedStep):
        return outcome
    reply = None if outcome.reply is None else _store_translation_result(outcome.reply, store)
    if isinstance(outcome, steps.AppliedStep):
        return StoredAppliedStep(reply=_required_reply(reply))
    return StoredFailedStep(diagnostic=outcome.diagnostic, reply=reply)


def _load_translation_outcome(
    outcome: StoredStepOutcome[StoredExtensionTranslationResult], resolver: BodyResolver,
) -> steps.StepOutcome[ExtensionTranslationResult]:
    if isinstance(outcome, steps.RejectedStep):
        return outcome
    if isinstance(outcome, StoredAppliedStep):
        return steps.AppliedStep(reply=_load_translation_result(outcome.reply, resolver))
    return steps.FailedStep(
        diagnostic=outcome.diagnostic,
        reply=None if outcome.reply is None else _load_translation_result(outcome.reply, resolver),
    )


def _store_canonical_outcome(
    outcome: steps.StepOutcome[transforms.CanonicalTransformResult], store: BodyStore,
) -> StoredStepOutcome[StoredTransformResult]:
    if isinstance(outcome, steps.RejectedStep):
        return outcome
    reply = None if outcome.reply is None else _store_canonical_result(outcome.reply, store)
    if isinstance(outcome, steps.AppliedStep):
        return StoredAppliedStep(reply=_required_reply(reply))
    return StoredFailedStep(diagnostic=outcome.diagnostic, reply=reply)


def _load_canonical_outcome(
    outcome: StoredStepOutcome[StoredTransformResult], resolver: BodyResolver,
) -> steps.StepOutcome[transforms.CanonicalTransformResult]:
    if isinstance(outcome, steps.RejectedStep):
        return outcome
    if isinstance(outcome, StoredAppliedStep):
        return steps.AppliedStep(reply=_load_canonical_result(outcome.reply, resolver))
    return steps.FailedStep(
        diagnostic=outcome.diagnostic,
        reply=None if outcome.reply is None else _load_canonical_result(outcome.reply, resolver),
    )
