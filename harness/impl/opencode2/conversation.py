# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate native OpenCode2 conversation events."""

from domain import event_actor, event_conversation, event_session, ids, messaging, outcomes, references, work_state
from domain.content import TextContent
from domain.event_base import EventPayload
from harness.impl.opencode2.records import NativeModel, NativeRecord


def payloads(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    """Read completed messages and execution state.

    Returns:
        The facts carried by this native event.

    """
    event_type = native_record.event.type
    model = (
        native_record.session.model
        if event_type == "baqylau.terminal.attached"
        else native_record.event.details.model
    )
    if event_type == "session.inbox.delivered":
        return _delivered(native_record)
    if event_type == "session.step.ended" and native_record.message is not None:
        return (_message(native_record),)
    if event_type in {"session.step.started", "baqylau.terminal.attached"} and model is not None:
        return _selection(model)
    if event_type == "session.reasoning.ended" and native_record.event.details.text:
        return (event_conversation.ReasoningCreated(
            ids.ReasoningId(native_record.event.id), TextContent(native_record.event.details.text),
        ),)
    return _session_events(native_record)


def _delivered(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    """Read what was put in front of the session.

    A person's text starts a turn and is kept as the prompt. A native notice
    wakes the session without a person: a finished background child is
    announced this way, and the native side answers it in a new turn of its
    own, so the record starts one here too.

    Returns:
        The facts carried by one delivered inbox item.

    """
    if native_record.prompt is not None:
        return _prompt(native_record)
    if native_record.notice is not None:
        return (event_conversation.TurnStarted(None),)
    return ()


def _selection(native_model: NativeModel) -> tuple[EventPayload, ...]:
    model_name = f"{native_model.provider}/{native_model.id}"
    changed = event_session.ModelChanged(
        None,
        references.ModelReference(model_name, model_name),
        work_state.ModelChangeReason.REPORTED_BY_HARNESS,
    )
    if native_model.variant is None:
        return (changed,)
    return (
        changed,
        event_session.EffortChanged(None, native_model.variant, work_state.EffortChangeReason.REPORTED_BY_HARNESS),
    )


def _prompt(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    message_id = ids.MessageId(str(native_record.event.details.inbox_id))
    names = tuple(f"[Attachment: {name}]" for name in native_record.attachment_names)
    text = "\n".join((*names, native_record.prompt or ""))
    return (
        event_conversation.TurnStarted(message_id),
        event_conversation.MessageCreated(
            message_id, messaging.MessageRole.USER, TextContent(text),
            messaging.MessagePhase.PROMPT, None,
        ),
    )


def _message(native_record: NativeRecord) -> EventPayload:
    details = native_record.event.details
    return event_conversation.MessageCreated(
        ids.MessageId(str(details.assistant_message_id)),
        messaging.MessageRole.ASSISTANT,
        TextContent(native_record.message or ""),
        messaging.MessagePhase.END_TURN if details.finish == "stop" else messaging.MessagePhase.INTERMEDIATE,
        None,
    )


def _session_events(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    event_type = native_record.event.type
    if event_type == "session.execution.succeeded":
        return (event_conversation.TurnFinished(None, outcomes.Outcome.SUCCEEDED),)
    if event_type == "session.execution.failed":
        return (event_conversation.TurnFinished(None, outcomes.Outcome.FAILED),)
    if event_type == "session.execution.interrupted":
        return (event_conversation.TurnAborted("OpenCode2 execution interrupted"),)
    if event_type == "session.renamed" and native_record.event.details.title:
        return (_title(native_record),)
    return ()


def _title(native_record: NativeRecord) -> EventPayload:
    title = native_record.event.details.title or ""
    if native_record.session.parent_id is not None:
        return event_actor.ActorNameChanged(title)
    return event_session.SessionTitleChanged(title, work_state.TitleOrigin.SUMMARY)
