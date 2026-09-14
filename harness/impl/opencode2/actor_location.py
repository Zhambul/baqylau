# Copyright (c) 2026 Zhambyl Yermagambet
"""Locate native child work inside the root conversation."""

from dataclasses import replace

from domain import ids
from harness.impl.opencode2.records import NativeRecord
from harness.models.raw_events import RawEvent


def child_id(native_record: NativeRecord) -> str | None:
    """Find the child named by a parent tool result.

    Returns:
        The native child identity, when this is a subagent tool event.

    """
    if native_record.tool is None or native_record.tool.name != "subagent":
        return None
    metadata = native_record.event.details.metadata
    return None if metadata is None else metadata.session_id


def located(raw_event: RawEvent) -> RawEvent:
    """Apply the recorded parent link to the raw event address.

    Returns:
        The event addressed to its root session and native actor.

    """
    record = NativeRecord.model_validate_json(raw_event.payload)
    child = child_id(record)
    actor = child or record.session.id
    parent = record.session.id if child else record.session.parent_id
    return replace(
        raw_event,
        session_id=record.root_id or ids.SessionId(record.session.id),
        actor_id=ids.ActorId(f"{actor}:lead"),
        parent_actor_id=None if parent is None else ids.ActorId(f"{parent}:lead"),
    )
