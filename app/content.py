"""Resolve frontend content references from canonical events."""

from __future__ import annotations

from domain.events import (
    AttentionResolved,
    ActorAssignmentFinished,
    ActorAssignmentStarted,
    FileAccessed,
    MessageCreated,
    OperationFinished,
    OperationInputProvided,
    OperationProgressed,
    OperationStarted,
    ActorMessageSent,
    ReasoningCreated,
)
from domain.ids import CanonicalEventId, OperationId
from domain.values import StructuredContent, TextContent
from runtime.event_store import EventStore
from runtime.projections import SessionQueries


class CanonicalContentService:
    def __init__(self, event_store: EventStore, queries: SessionQueries) -> None:
        self.event_store = event_store
        self.queries = queries

    def resolve(self, content_reference: str) -> str:
        event_id, separator, field_name = content_reference.rpartition(":")
        if not separator or not event_id or not field_name:
            raise ValueError("invalid content reference")
        stored_event = self.event_store.event(CanonicalEventId(event_id))
        if stored_event is None:
            raise KeyError(content_reference)
        payload = stored_event.event.payload
        if field_name in {
            "operation_command",
            "operation_output",
            "operation_content",
        } and isinstance(
            payload,
            (OperationStarted, OperationProgressed, OperationFinished),
        ):
            activity = self.queries.operation_activity(
                stored_event.event.session_id,
                stored_event.event.actor_id,
                OperationId(str(payload.operation_id)),
                stored_event.cursor,
            )
            if field_name == "operation_command":
                return activity.command_text()
            if field_name == "operation_output":
                return activity.output_text()
            if activity.result is not None:
                content_values = (activity.result,)
            elif activity.progress:
                content_values = activity.current_progress()
            elif activity.arguments is not None:
                content_values = (activity.arguments,)
            else:
                content_values = ()
            return "\n".join(
                value.text if isinstance(value, TextContent) else value.json_text
                for value in content_values
            )
        allowed_fields = {
            MessageCreated: frozenset({"content"}),
            ReasoningCreated: frozenset({"content"}),
            OperationProgressed: frozenset({"content"}),
            OperationInputProvided: frozenset({"content"}),
            FileAccessed: frozenset({"content", "unified_diff"}),
            ActorAssignmentStarted: frozenset({"brief"}),
            ActorAssignmentFinished: frozenset({"result"}),
            AttentionResolved: frozenset({"feedback"}),
            ActorMessageSent: frozenset({"content"}),
        }.get(type(payload), frozenset())
        if field_name not in allowed_fields:
            raise KeyError(content_reference)
        value = getattr(payload, field_name)
        if value is None:
            return ""
        if isinstance(value, TextContent):
            return value.text
        if isinstance(value, StructuredContent):
            return value.json_text
        if not isinstance(value, str):
            raise TypeError("referenced content is not textual")
        return value
