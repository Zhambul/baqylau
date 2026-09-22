# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep original session state outside extension-controlled activity."""

import hashlib

from baqylau_extension_api.core import actors, sessions
from baqylau_extension_api.models.canonical import CanonicalFact, CoreFact

from domain.records import RecordedTranslationDecision
from extensions.models.interpretation_context import InterpretationContext
from extensions.models.interpretation_steps import CoreLifecycleStep
from extensions.models.observations import ExtensionObservation
from extensions.models.processing_input import original_bytes


def required_fact(fact: CanonicalFact) -> bool:
    """Identify the facts reserved for the original lifecycle pass.

    Returns:
        Whether the fact starts or finishes a session, or starts its lead actor.

    """
    if not isinstance(fact, CoreFact):
        return False
    payload = fact.payload
    return isinstance(payload, sessions.SessionStarted | sessions.SessionFinished) or (
        isinstance(payload, actors.ActorStarted) and payload.role == "lead"
    )


def require_activity(facts: tuple[CanonicalFact, ...]) -> None:
    """Reject a complete derived result which contains required session state.

    Raises:
        ValueError: If an activity decoder or transform proposes a required fact.

    """
    if any(required_fact(fact) for fact in facts):
        message = "derived activity cannot create required session lifecycle facts"
        raise ValueError(message)


def validate_lifecycle(
    context: InterpretationContext, step: CoreLifecycleStep, translator_version: str,
) -> None:
    """Check the original byte reference and required output without a second decoder call.

    Raises:
        TypeError: If an extension original supplies a host lifecycle pass.
        ValueError: If the step changes its original, version, fact group, or verdict.

    """
    original = context.original.observation
    if isinstance(original, ExtensionObservation):
        message = "extension input has no host lifecycle pass"
        raise TypeError(message)
    _validate_original(context, step)
    only_required = all(required_fact(fact) for fact in step.facts)
    if (
        step.translator_version != translator_version or not only_required
    ):
        message = "required lifecycle step changed its decoder or fact group"
        raise ValueError(message)
    if bool(step.facts) != (step.decision == RecordedTranslationDecision.TRANSLATED):
        message = "required lifecycle verdict does not match its output"
        raise ValueError(message)


def combined_facts(
    required: tuple[CoreFact, ...], activity: tuple[CanonicalFact, ...],
) -> tuple[CanonicalFact, ...]:
    """Place starts before activity and finishes after it so cleanup runs last.

    Returns:
        Complete ordered facts, with first-body selection for repeated required IDs.

    """
    seen: set[str] = set()
    unique: list[CoreFact] = []
    for original_fact in required:
        if original_fact.event_id not in seen:
            seen.add(original_fact.event_id)
            unique.append(original_fact)
    starts = tuple(
        fact for fact in unique if not isinstance(fact.payload, sessions.SessionFinished)
    )
    finishes = tuple(
        fact for fact in unique if isinstance(fact.payload, sessions.SessionFinished)
    )
    return (*starts, *activity, *finishes)


def require_separate_ids(required: tuple[CoreFact, ...], activity: tuple[CanonicalFact, ...]) -> None:
    """Keep an activity result from using an original required fact ID.

    Raises:
        ValueError: If an activity fact uses a reserved lifecycle identity.

    """
    reserved = frozenset(fact.event_id for fact in required)
    if any(fact.event_id in reserved for fact in activity):
        message = "activity reused a required lifecycle identity"
        raise ValueError(message)


def _validate_original(context: InterpretationContext, step: CoreLifecycleStep) -> None:
    content = original_bytes(context.original)
    expected = len(content), hashlib.sha256(content).hexdigest()
    if (step.content_byte_length, step.content_digest) != expected:
        message = "required lifecycle step changed its original byte reference"
        raise ValueError(message)
