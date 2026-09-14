# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate native file results without reading changed files again."""

from pathlib import Path

from domain import content, event_resource, outcomes
from domain.event_base import EventPayload
from harness.impl.opencode2 import file_changes
from harness.impl.opencode2.records import NativeRecord

WRITE_TOOL = "write"
# The patch tool applies one text patch that can add, change, delete, and move
# files. It gives no path of its own: it names each file it wrote in its result.
FILE_TOOLS = frozenset(("read", WRITE_TOOL, "edit", "patch"))


def payloads(native_record: NativeRecord) -> tuple[EventPayload, ...]:
    """Read completed native file operations.

    Returns:
        File facts with the native result and path.

    """
    tool = native_record.tool
    if tool is None or tool.name not in FILE_TOOLS:
        return ()
    if native_record.event.type not in {"session.tool.success", "session.tool.failed"}:
        return ()
    metadata = native_record.event.details.metadata
    if metadata is not None and metadata.files:
        directory = native_record.session.location.directory
        outcome = _outcome(native_record)
        return tuple(file_changes.payload(directory, outcome, change) for change in metadata.files)
    if tool.input is None or tool.input.path is None:
        return ()
    return (_single(native_record, tool.name, tool.input.path, tool.input.content),)


def _single(native_record: NativeRecord, name: str, path: str, text: str | None) -> event_resource.FileAccessed:
    result = _text(native_record)
    action = _action(name, result)
    added = None if text is None else len(text.splitlines())
    return event_resource.FileAccessed(
        str(Path(native_record.session.location.directory) / path), action, _outcome(native_record),
        lines_added=added if name == WRITE_TOOL else None,
        content=content.TextContent(text if name == WRITE_TOOL and text is not None else result),
    )


def _action(name: str, text: str) -> outcomes.FileAction:
    if name == "read":
        return outcomes.FileAction.READ
    if name == WRITE_TOOL and text.startswith("Created file successfully:"):
        return outcomes.FileAction.CREATED
    return outcomes.FileAction.UPDATED


def _outcome(native_record: NativeRecord) -> outcomes.Outcome:
    return outcomes.Outcome.SUCCEEDED if native_record.event.type == "session.tool.success" else outcomes.Outcome.FAILED


def _text(native_record: NativeRecord) -> str:
    error = native_record.event.details.error
    if error is not None:
        return error.message
    parts = native_record.event.details.content
    return "\n".join(part.text for part in parts if part.text is not None)
