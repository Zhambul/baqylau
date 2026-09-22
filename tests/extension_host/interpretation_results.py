# Copyright (c) 2026 Zhambyl Yermagambet
"""Change complete typed proposals while keeping their source evidence explicit."""

from baqylau_extension_api.models.canonical import CanonicalFact
from baqylau_extension_api.models.translation_results import ExtensionTranslationResult, TranslatedFact, TranslatedInput
from baqylau_extension_api.translation.results import translated_candidates

from domain.records import RecordedTranslationDecision
from extensions.models.interpretation_steps import AppliedStep, ExtensionTranslationStep
from extensions.models.interpretations import InterpretationCommit


def translation(request: InterpretationCommit) -> ExtensionTranslationStep:
    """Select the fixture's single original decoder step.

    Returns:
        Its typed decoder evidence.

    """
    selected = (step for step in request.proposal.steps if isinstance(step, ExtensionTranslationStep))
    step = next(selected, None)
    assert step is not None
    return step


def reply(request: InterpretationCommit) -> ExtensionTranslationResult:
    """Select a successful decoder response.

    Returns:
        The complete response, including all per-input decisions.

    """
    outcome = translation(request).outcome
    assert isinstance(outcome, AppliedStep)
    return outcome.reply


def with_reply(request: InterpretationCommit, response: ExtensionTranslationResult) -> InterpretationCommit:
    """Keep final candidates consistent with a changed decoder response.

    Returns:
        A complete one-step proposal for repository validation.

    """
    step = translation(request).model_copy(update={"outcome": AppliedStep(reply=response)})
    return request.model_copy(update={"proposal": request.proposal.model_copy(update={
        "facts": translated_candidates(response), "steps": (step,),
    })})


def with_fact(request: InterpretationCommit, fact: CanonicalFact) -> InterpretationCommit:
    """Replace the fixture's one logical fact without changing its stable key.

    Returns:
        A complete proposal with the later body in both trace and final output.

    """
    response = reply(request)
    decision = response.decisions[0]
    assert isinstance(decision, TranslatedInput)
    changed = decision.model_copy(update={
        "facts": (TranslatedFact(fact_key="message-1", fact=fact),),
    })
    return with_reply(request, response.model_copy(update={"decisions": (changed,)}))


def empty_decision(
    request: InterpretationCommit, response: ExtensionTranslationResult, decision: RecordedTranslationDecision,
) -> InterpretationCommit:
    """Keep an empty decoder result distinct from a transform drop.

    Returns:
        Complete source evidence with the selected empty verdict.

    """
    changed = with_reply(request, response)
    return changed.model_copy(update={"proposal": changed.proposal.model_copy(update={
        "decision": decision, "reason": "Recorded decoder decision",
    })})
