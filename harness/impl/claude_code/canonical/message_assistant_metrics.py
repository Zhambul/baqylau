# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate Claude Code assistant model, usage, and turn metrics."""

from harness.impl.claude_code.canonical import (
    message_assistant_dependencies as dependencies,
    message_models,
    message_subject_values,
    records,
)
from harness.models import selections


def assistant_model_events(
    source: message_models.TranscriptSource,
    response: message_models.AssistantResponse,
    selection_semantics: selections.SelectionSemantics,
) -> list[dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]]:
    """Report a changed model selection from the assistant response.

    Returns:
        The model event, or no events if the model is absent or unchanged.

    """
    if response.model.reference is None:
        return []
    reported = selection_semantics.model(
        source.raw_event.session_id,
        source.raw_event.actor_id,
        response.model.reference,
        dependencies.work_state.ModelChangeReason.REPORTED_BY_HARNESS,
        dependencies.model_names.selection_key(response.model.model_name or ""),
    )
    if reported is None:
        return []
    draft = dependencies.raw_event_builders.CanonicalEventDraft(
        message_subject_values.MODEL_SUBJECT,
        source.native_identity,
        "reported",
        reported,
        occurred_at=source.occurred_at,
    )
    return [dependencies.support.event(source.raw_event, draft)]


def assistant_usage_events(
    source: message_models.TranscriptSource,
    response: message_models.AssistantResponse,
) -> list[dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]]:
    """Report context size and token usage from the assistant response.

    Returns:
        Context and usage events, or no events without usage and model data.

    """
    usage_record = response.message.usage if response.message else None
    if usage_record is None or response.model.reference is None:
        return []
    cache_write_tokens = _cache_write_tokens(usage_record)
    context_draft = dependencies.raw_event_builders.CanonicalEventDraft(
        "context",
        source.native_identity,
        "reported",
        dependencies.event_telemetry.ContextReported(
            dependencies.model.context_used(usage_record),
            dependencies.model.context_window(response.model.model_name),
            response.model.reference,
        ),
        occurred_at=source.occurred_at,
    )
    usage_draft = dependencies.raw_event_builders.CanonicalEventDraft(
        "usage",
        source.native_identity,
        "reported",
        dependencies.event_telemetry.UsageReported(
            scope=dependencies.usage.UsageScope.SESSION,
            subject_id=str(source.raw_event.session_id),
            model=response.model.reference,
            account=None,
            tokens=dependencies.usage.TokenUsage(
                input_tokens=int(usage_record.input_tokens or 0),
                output_tokens=int(usage_record.output_tokens or 0),
                cache_read_tokens=int(usage_record.cache_read_input_tokens or 0),
                cache_write_tokens=cache_write_tokens[0],
                one_hour_cache_write_tokens=cache_write_tokens[1],
            ),
            cumulative=False,
            cost_in_usd=None,
        ),
        occurred_at=source.occurred_at,
    )
    return [
        dependencies.support.event(source.raw_event, context_draft),
        dependencies.support.event(source.raw_event, usage_draft),
    ]


def _cache_write_tokens(usage_record: records.MessageUsage) -> tuple[int, int]:
    cache_creation = usage_record.cache_creation
    if cache_creation is None:
        return int(usage_record.cache_creation_input_tokens or 0), 0
    return (
        int(cache_creation.ephemeral_five_minute_input_tokens),
        int(cache_creation.ephemeral_one_hour_input_tokens),
    )


def assistant_turn_events(
    source: message_models.TranscriptSource,
    response: message_models.AssistantResponse,
    semantics: message_models.TranscriptSemantics,
) -> list[dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]]:
    """Finish a turn when its final assistant response arrives.

    Returns:
        The turn-finished event, or no events if no turn can be finished.

    """
    if not response.ends_turn:
        return []
    native_identity = source.native_identity
    if response.message is not None and response.message.id:
        native_identity = str(response.message.id)
    native_message_id = dependencies.ids.ClaudeCodeMessageId(native_identity)
    turn_id = semantics.turns.finished_by_transcript(
        source.raw_event,
        native_message_id,
        semantics.recovered_turn_id,
    )
    if turn_id is None:
        return []
    final_message_id = (
        dependencies.ids.message_id_from_claude_code(
            dependencies.ids.ClaudeCodeMessageId(f"{source.native_identity}:{response.last_text_index}"),
        )
        if response.last_text_index >= 0
        else None
    )
    draft = dependencies.raw_event_builders.CanonicalEventDraft(
        message_subject_values.TURN_SUBJECT,
        str(turn_id),
        "finished",
        dependencies.event_conversation.TurnFinished(final_message_id, dependencies.outcomes.Outcome.SUCCEEDED),
        turn_id=turn_id,
        occurred_at=source.occurred_at,
    )
    return [dependencies.support.event(source.raw_event, draft)]
