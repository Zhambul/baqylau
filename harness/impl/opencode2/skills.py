# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate the native skill tool lifecycle."""

from domain import content, event_resource, ids, outcomes
from domain.event_base import EventPayload
from harness.impl.opencode2.records import NativeRecord


def payloads(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    """Keep a loaded skill and its result on the calling worker.

    Returns:
        A native start or finish, or no skill fact for another tool.

    """
    tool = native_record.tool
    details = native_record.event.details
    session = native_record.session
    if (
        tool is None or tool.name != "skill" or tool.input is None
        or tool.input.skill_id is None
    ):
        return ()
    if details.id is None:
        return ()
    skill_id = ids.SkillId(f"{session.id}:{details.id}")
    if native_record.event.type == "session.tool.called":
        return (event_resource.SkillStarted(skill_id, str(tool.input.skill_id), None),)
    if native_record.event.type in {"session.tool.success", "session.tool.failed"}:
        outcome = outcomes.Outcome.FAILED
        if native_record.event.type == "session.tool.success":
            outcome = outcomes.Outcome.SUCCEEDED
        return (event_resource.SkillFinished(skill_id, outcome, _result(native_record)),)
    return ()


def _result(native_record: NativeRecord) -> content.TextContent:
    error = native_record.event.details.error
    if error is not None:
        return content.TextContent(error.message)
    return content.TextContent("\n".join(
        part.text for part in native_record.event.details.content if part.text is not None
    ))
