# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep native web results on the worker that requested them."""

from domain import content, event_resource, outcomes
from domain.event_base import EventPayload
from harness.impl.opencode2.records import NativeRecord


def payloads(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    """Read completed native web tool calls.

    Returns:
        Web facts with saved input and the native result.

    """
    tool = native_record.tool
    if tool is None or tool.input is None:
        return ()
    if native_record.event.type not in {"session.tool.success", "session.tool.failed"}:
        return ()
    outcome = outcomes.Outcome.SUCCEEDED
    if native_record.event.type == "session.tool.failed":
        outcome = outcomes.Outcome.FAILED
    result = _result(native_record)
    if tool.name == "webfetch" and tool.input.url is not None:
        return (event_resource.WebFetched(tool.input.url, result, outcome),)
    if tool.name == "websearch" and tool.input.query is not None:
        return (event_resource.SearchPerformed(
            tool.name, content.TextContent(tool.input.query), result, outcome,
        ),)
    return ()


def _result(native_record: NativeRecord) -> content.TextContent:
    error = native_record.event.details.error
    if error is not None:
        return content.TextContent(error.message)
    return content.TextContent("\n".join(
        part.text for part in native_record.event.details.content if part.text is not None
    ))
