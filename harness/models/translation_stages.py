# Copyright (c) 2026 Zhambyl Yermagambet
"""Separate required session state from activity controlled by extensions."""

from enum import StrEnum

from domain import event_actor, event_base, event_session, messaging, records
from harness.models.raw_events import TranslationResult


class TranslationStage(StrEnum):
    """Select one pass; COMPLETE keeps the existing single-pass behavior."""

    COMPLETE = "complete"
    LIFECYCLE = "lifecycle"
    ACTIVITY = "activity"


def required_event(event: event_base.CanonicalEvent[event_base.EventPayload]) -> bool:
    """Identify the session facts which the host must preserve.

    Returns:
        Whether the fact starts a session or its lead actor, or finishes a session.

    """
    payload = event.payload
    return isinstance(payload, event_session.SessionStarted | event_session.SessionFinished) or (
        isinstance(payload, event_actor.ActorStarted) and payload.role == messaging.ActorRole.LEAD
    )


def empty_result(translation_stage: TranslationStage) -> TranslationResult:
    """Return an explicit decision for an input with no work in this pass.

    Returns:
        A nonsemantic decision with no canonical facts.

    """
    return TranslationResult(
        (), records.RecordedTranslationDecision.IGNORED_NONSEMANTIC, f"no {translation_stage.value} events",
    )


def select_result(translation_result: TranslationResult, translation_stage: TranslationStage) -> TranslationResult:
    """Select facts without running a translator or changing its memory.

    Lifecycle callers must use a lifecycle-only decoder before this selection.
    Filtering a complete stateful translation is not a lifecycle pass.

    Returns:
        The selected facts, or the existing empty decision.

    """
    if translation_stage == TranslationStage.COMPLETE or not translation_result.canonical_events:
        return translation_result
    selected = tuple(event for event in translation_result.canonical_events
                     if required_event(event) == (translation_stage == TranslationStage.LIFECYCLE))
    if not selected:
        return empty_result(translation_stage)
    return TranslationResult(selected, records.RecordedTranslationDecision.TRANSLATED, translation_result.reason)
