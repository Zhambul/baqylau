# Copyright (c) 2026 Zhambyl Yermagambet
"""Build Claude Code prompt outcome events."""

from dataclasses import replace

from domain import event_base, event_conversation as conversation_events
from harness.impl.claude_code.canonical import (
    message_models,
    message_prompt_dependencies as dependencies,
    message_subject_values as subject_values,
)
from harness.impl.claude_code.canonical.message_commands import prompt_turn


def interrupted_prompt_events(
    source: message_models.TranscriptSource,
    prompt_message: message_models.PromptMessage,
) -> list[event_base.CanonicalEvent[event_base.EventPayload]]:
    """Build events for an interrupted prompt.

    Returns:
        The message followed by a turn abort event if one is still needed.

    """
    if prompt_message.interruption.abort_already_emitted:
        return [prompt_message.created]
    turn_id = prompt_message.interruption.turn_id
    draft = dependencies.raw_event_builders.CanonicalEventDraft(
        subject_values.TURN_SUBJECT,
        str(turn_id) if turn_id else source.native_identity,
        "aborted",
        conversation_events.TurnAborted(None),
        turn_id=turn_id,
        occurred_at=source.occurred_at,
    )
    return [prompt_message.created, dependencies.support.event(source.raw_event, draft)]


def resumed_prompt_events(
    source: message_models.TranscriptSource,
    prompt_message: message_models.PromptMessage,
    turn_semantics: dependencies.turns.TurnSemantics,
) -> list[event_base.CanonicalEvent[event_base.EventPayload]]:
    """Resume a turn and attach its identifier to the prompt.

    Returns:
        Turn start events followed by the prompt message.

    """
    started = prompt_turn(source, turn_semantics)
    created = replace(prompt_message.created, turn_id=turn_semantics.current(source.raw_event))
    return [*started, created]


def user_prompt_events(
    source: message_models.TranscriptSource,
    prompt_message: message_models.PromptMessage,
    turn_semantics: dependencies.turns.TurnSemantics,
) -> list[event_base.CanonicalEvent[event_base.EventPayload]]:
    """Start a user prompt turn after any replaced turn is aborted.

    Returns:
        An optional abort, turn start events, and the prompt message, in that order.

    """
    interruption = prompt_message.interruption
    interrupted = []
    if interruption.turn_id is not None:
        draft = dependencies.raw_event_builders.CanonicalEventDraft(
            subject_values.TURN_SUBJECT,
            str(interruption.turn_id),
            "aborted",
            conversation_events.TurnAborted(None),
            turn_id=interruption.turn_id,
            occurred_at=source.occurred_at,
        )
        interrupted.append(dependencies.support.event(source.raw_event, draft))
    started = prompt_turn(source, turn_semantics)
    created = replace(prompt_message.created, turn_id=turn_semantics.current(source.raw_event))
    return [*interrupted, *started, created]
