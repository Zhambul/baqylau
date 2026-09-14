# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate the native OpenCode2 compaction lifecycle."""

from domain.content import TextContent
from domain.event_telemetry import CompactionFinished, CompactionStarted
from harness.impl.opencode2.records import NativeRecord

STARTED = "session.compaction.started"
ENDED = "session.compaction.ended"


def payloads(native_record: NativeRecord) -> tuple[CompactionStarted | CompactionFinished, ...]:
    """Read the start and end of one native compaction.

    The native records carry no token count of their own. The context size
    before and after belongs to the steps around them, which report it
    separately, so it is not repeated here as a guess.

    Returns:
        The compaction facts carried by this native event.

    """
    event_type = native_record.event.type
    if event_type == STARTED:
        return (CompactionStarted(None),)
    if event_type == ENDED:
        # The native end record carries the kept context. A failed compaction
        # has its own record and keeps nothing, so it does not end one here.
        return (CompactionFinished(None, None, TextContent(native_record.event.details.text or "")),)
    return ()
