# Copyright (c) 2026 Zhambyl Yermagambet
"""Read saved OpenCode2 events after file change notices."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from domain import ids
from harness import file_tail
from harness.contract import HarnessRawEventSource, HarnessRawEventSources
from harness.impl.opencode2.actor_location import located
from harness.models.raw_events import RawEvent, RawEventSourceContext

if TYPE_CHECKING:
    from harness.models.session import Session

HARNESS = ids.HarnessName("opencode2")
READ_LIMIT = 100


class OpenCodeSource(HarnessRawEventSource):
    """Read complete native log lines with stable byte positions."""

    def __init__(self, raw_event_source_context: RawEventSourceContext) -> None:
        """Open the session's local event log."""
        self.raw_event_source_context = raw_event_source_context
        self.source_identity = f"opencode2:events:{raw_event_source_context.source_reference}"
        self.tail = file_tail.CompleteLineTail(raw_event_source_context.source_reference)

    def watch_paths(self) -> tuple[str, ...]:
        """List the event log.

        Returns:
            The path that wakes the interpreter.

        """
        return (self.raw_event_source_context.source_reference,)

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        """Read new complete records.

        Returns:
            The records after the saved byte position.

        """
        return tuple(
            located(RawEvent(
                raw_event_id=ids.RawEventId(f"{self.source_identity}:{line.position}"),
                harness=HARNESS,
                source_type="native",
                source_name=self.raw_event_source_context.source_reference,
                source_position=str(line.position),
                session_id=self.raw_event_source_context.session_id,
                actor_id=self.raw_event_source_context.actor_id,
                parent_actor_id=self.raw_event_source_context.parent_actor_id,
                observed_at=time.time(),
                encoding="jsonl",
                payload=line.content,
                source_identity=self.source_identity,
            ))
            for line in self.tail.read(after_position, READ_LIMIT)
        )


class OpenCodeSources(HarnessRawEventSources):
    """Build readers for registered OpenCode2 sessions."""

    def for_session(self, session: Session) -> tuple[OpenCodeSource, ...]:
        """Build the event reader.

        Returns:
            The session's file-backed source.

        """
        return (OpenCodeSource(session.source_context),)

    def release_session(self, session_id: ids.SessionId) -> None:
        """No file handles are held between reads."""
