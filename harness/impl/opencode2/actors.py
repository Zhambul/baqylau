# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate native subagent assignments and completion."""

from types import MappingProxyType

from domain import event_actor, ids, outcomes
from domain.content import TextContent
from domain.event_base import EventPayload
from harness.impl.opencode2.actor_location import child_id
from harness.impl.opencode2.records import NativeRecord

OUTCOMES = MappingProxyType({
    "session.execution.succeeded": outcomes.Outcome.SUCCEEDED,
    "session.execution.failed": outcomes.Outcome.FAILED,
    "session.execution.interrupted": outcomes.Outcome.CANCELLED,
})


def payloads(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    """Read native assignment facts.

    Returns:
        A child assignment start or its final result.

    """
    child = child_id(native_record)
    if child is not None:
        return _assignment(native_record, child)
    # A later shell notice can wake a child after its assignment has ended.
    # That notice has no assignment turn and must not replace its result.
    if native_record.session.parent_id is None or native_record.turn_id is None:
        return ()
    outcome = OUTCOMES.get(native_record.event.type)
    if outcome is None:
        return ()
    error = native_record.event.details.error
    reason = None if error is None else error.message
    result = None
    if outcome == outcomes.Outcome.SUCCEEDED:
        result = TextContent(native_record.message or "")
    return (
        event_actor.ActorAssignmentFinished(
            ids.AssignmentId(native_record.session.id), outcome, result, reason,
        ),
        event_actor.ActorFinished(reason),
    )


def _assignment(native_record: NativeRecord, child: str) -> tuple[EventPayload, ...]:
    tool = native_record.tool
    if native_record.event.type != "session.tool.progress" or tool is None or tool.input is None:
        return ()
    return (event_actor.ActorAssignmentStarted(
        ids.AssignmentId(child), TextContent(tool.input.description or "subagent"),
        tool.input.description, TextContent(tool.input.prompt or ""),
    ),)
