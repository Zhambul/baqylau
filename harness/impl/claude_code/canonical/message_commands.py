# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate Claude Code prompt turns and slash commands."""

from domain import event_base, event_conversation as conversation_events, messaging, work_state
from harness.impl.claude_code.canonical import (
    message_command_dependencies as dependencies,
    message_models,
    message_subject_values,
    transcript,
)
from harness.models import raw_event_builders, raw_events


def prompt_turn(
    raw_event: raw_events.RawEvent,
    turn_semantics: dependencies.turns.TurnSemantics,
    native_identity: str,
    occurred_at: float | None,
    *,
    prompt_message_identity: str | None = None,
) -> list[event_base.CanonicalEvent[event_base.EventPayload]]:
    """Open the turn for a user prompt.

    Returns:
        The turn events.

    """
    turn_id = dependencies.ids.turn_id_from_claude_code(
        dependencies.ids.ClaudeCodeTurnId(native_identity),
    )
    if not turn_semantics.begin(raw_event, turn_id):
        return []
    message_identity = prompt_message_identity or native_identity
    message_id = dependencies.ids.message_id_from_claude_code(
        dependencies.ids.ClaudeCodeMessageId(message_identity),
    )
    draft = raw_event_builders.CanonicalEventDraft(
        message_subject_values.TURN_SUBJECT,
        str(turn_id),
        message_subject_values.STARTED_PHASE,
        conversation_events.TurnStarted(message_id),
        turn_id=turn_id,
        occurred_at=occurred_at,
    )
    return [dependencies.support.event(raw_event, draft)]


def slash_command(
    source: message_models.TranscriptSource,
    record: transcript.SlashCommandTranscriptRecord,
    semantics: message_models.TranscriptSemantics,
) -> list[event_base.CanonicalEvent[event_base.EventPayload]]:
    """Translate a slash command.

    Returns:
        The command events.

    """
    name = record.name.lstrip("/").strip().lower()
    selection = record.arguments.strip()
    if name in {"clear", "rename"}:
        return []
    if _is_selection_command(name, selection):
        return _selection_command_events(source, name, selection, semantics)
    command_event, role = _prompt_command_event(source, record)
    if role == messaging.MessageRole.USER:
        return [
            *prompt_turn(
                source.raw_event,
                semantics.turns,
                source.native_identity,
                source.occurred_at,
            ),
            command_event,
        ]
    return [command_event]


def _selection_command_events(
    source: message_models.TranscriptSource,
    name: str,
    selection: str,
    semantics: message_models.TranscriptSemantics,
) -> list[event_base.CanonicalEvent[event_base.EventPayload]]:
    selected_event = _slash_selection_event(source, name, selection, semantics)
    return [] if selected_event is None else [selected_event]


def _prompt_command_event(
    source: message_models.TranscriptSource,
    record: transcript.SlashCommandTranscriptRecord,
) -> tuple[event_base.CanonicalEvent[event_base.EventPayload], messaging.MessageRole]:
    role = messaging.MessageRole.USER if source.raw_event.parent_actor_id is None else messaging.MessageRole.PARENT
    message_id = dependencies.ids.message_id_from_claude_code(
        dependencies.ids.ClaudeCodeMessageId(source.native_identity),
    )
    draft = raw_event_builders.CanonicalEventDraft(
        message_subject_values.MESSAGE_SUBJECT,
        source.native_identity,
        message_subject_values.CREATED_PHASE,
        conversation_events.MessageCreated(
            message_id,
            role,
            dependencies.support.content(record.text),
            messaging.MessagePhase.PROMPT,
            None,
        ),
        occurred_at=source.occurred_at,
    )
    return dependencies.support.event(source.raw_event, draft), role


def _slash_selection_event(
    source: message_models.TranscriptSource,
    name: str,
    selection: str,
    semantics: message_models.TranscriptSemantics,
) -> event_base.CanonicalEvent[event_base.EventPayload] | None:
    payload = (
        semantics.selections.model(
            source.raw_event.session_id,
            source.raw_event.actor_id,
            dependencies.support.model_reference(dependencies.model.ClaudeCodeModel(selection)),
            work_state.ModelChangeReason.SELECTED,
            dependencies.model_names.selection_key(selection),
        )
        if name == message_subject_values.MODEL_SUBJECT
        else semantics.selections.effort(
            source.raw_event.session_id,
            source.raw_event.actor_id,
            selection,
            work_state.EffortChangeReason.SELECTED,
        )
    )
    if payload is None:
        return None
    draft = raw_event_builders.CanonicalEventDraft(
        name,
        source.native_identity,
        "selected",
        payload,
        occurred_at=source.occurred_at,
    )
    return dependencies.support.event(source.raw_event, draft)


def _is_selection_command(name: str, selection: str) -> bool:
    if name not in {message_subject_values.MODEL_SUBJECT, "effort"}:
        return False
    return bool(selection) and len(selection.split()) == 1
