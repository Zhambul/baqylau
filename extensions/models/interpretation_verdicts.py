# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep empty verdicts consistent with the recorded decoder and transform decisions."""

from baqylau_extension_api.models import translation_results as replies
from baqylau_extension_api.models.canonical import CanonicalFact
from baqylau_extension_api.models.transforms import Drop

from domain.records import RecordedTranslationDecision as Decision
from extensions.models import interpretation_steps as steps
from extensions.models.interpretations import InterpretationProposal


def require_verdict(proposal: InterpretationProposal) -> None:
    """Prevent intentional suppression from hiding an unknown input or decoder failure.

    Raises:
        ValueError: If the final verdict differs from the complete recorded evidence.

    """
    if proposal.decision != interpretation_verdict(proposal.facts, proposal.steps):
        message = "interpretation verdict does not match its recorded decisions"
        raise ValueError(message)


def interpretation_verdict(facts: tuple[CanonicalFact, ...], journal: tuple[steps.InterpretationStep, ...]) -> Decision:
    """Select the final verdict from complete processing evidence.

    Returns:
        Acceptance, suppression, or the recorded decoder failure.

    """
    if facts:
        return Decision.TRANSLATED
    recorded = _translation_verdicts(journal)
    if Decision.TRANSLATION_FAILED in recorded or _has_limit(journal):
        return Decision.TRANSLATION_FAILED
    if Decision.IGNORED_UNKNOWN in recorded:
        return Decision.IGNORED_UNKNOWN
    if Decision.TRANSLATED in recorded or _has_drop(journal):
        return Decision.SUPPRESSED
    return Decision.IGNORED_NONSEMANTIC


def _translation_verdicts(journal: tuple[steps.InterpretationStep, ...]) -> set[Decision]:
    recorded: set[Decision] = set()
    for step in journal:
        if isinstance(step, steps.CoreTranslationStep | steps.CoreActivityStep | steps.CoreLifecycleStep):
            recorded.add(step.decision)
        elif isinstance(step, steps.ExtensionTranslationStep):
            recorded.update(_extension_verdicts(step))
    return recorded


def _extension_verdicts(step: steps.ExtensionTranslationStep) -> set[Decision]:
    if isinstance(step.outcome, steps.FailedStep | steps.RejectedStep):
        return {Decision.TRANSLATION_FAILED}
    return {_decoder_verdict(decision) for decision in step.outcome.reply.decisions}


def _decoder_verdict(decision: replies.TranslationDecision) -> Decision:
    if isinstance(decision, replies.FailedInput):
        return Decision.TRANSLATION_FAILED
    if isinstance(decision, replies.UnsupportedInput):
        return Decision.IGNORED_UNKNOWN
    if isinstance(decision, replies.TranslatedInput):
        return Decision.TRANSLATED
    return Decision.IGNORED_NONSEMANTIC


def _has_limit(journal: tuple[steps.InterpretationStep, ...]) -> bool:
    return any(isinstance(step, steps.LimitStep) for step in journal)


def _has_drop(journal: tuple[steps.InterpretationStep, ...]) -> bool:
    transforms = (step for step in journal if isinstance(step, steps.RawTransformStep | steps.CanonicalTransformStep))
    for step in transforms:
        if isinstance(step.outcome, steps.AppliedStep) and any(
            isinstance(operation, Drop) for operation in step.outcome.reply.operations
        ):
            return True
    return False
