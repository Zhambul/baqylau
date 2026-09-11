# Copyright (c) 2026 Zhambyl Yermagambet
"""Read durable Claude Code teammate-idle events."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from pydantic import ValidationError

from domain import ids as domain_ids
from harness import contract as harness_contract, file_tail
from harness.impl.claude_code import ids as claude_ids
from harness.impl.claude_code.canonical import transcript
from harness.models import raw_events as raw_event_models

HARNESS = domain_ids.HarnessName("claude_code")
TEXT_ENCODING = "utf-8"


class ClaudeTeammateIdleRawEventSource(harness_contract.HarnessRawEventSource):
    """Reconcile durable Claude team completions."""

    event_batch_size = 500
    reconcile_tail_bytes = 1_000_000

    def __init__(self, raw_event_source_context: raw_event_models.RawEventSourceContext) -> None:
        """Initialize the teammate-idle source."""
        self.context = raw_event_source_context
        self.source_path = os.path.realpath(raw_event_source_context.source_reference)
        self.tail = file_tail.CompleteLineTail(self.source_path)
        source_hash = hashlib.sha256(self.source_path.encode(TEXT_ENCODING)).hexdigest()
        self.source_identity = f"claude_code:teammate_idle:{source_hash}"
        self._scan_position: str | None = None
        self._complete = False

    def read(self, after_position: str | None) -> tuple[raw_event_models.RawEvent, ...]:
        """Return read.

        Returns:
            Read.

        """
        if self._complete:
            return ()
        start, source_exists = self._starting_position(after_position)
        if not source_exists:
            return ()
        lines = self.tail.read(start, self.event_batch_size)
        self._store_progress(lines)
        return tuple(raw_event for line in lines for raw_event in self._idle_events(line))

    def _starting_position(self, after_position: str | None) -> tuple[str | None, bool]:
        if self._scan_position is not None:
            return self._scan_position, True
        if after_position is not None:
            return after_position, True
        try:
            size = Path(self.source_path).stat().st_size
        except OSError:
            self._complete = True
            return None, False
        if size > self.reconcile_tail_bytes:
            return str(size - self.reconcile_tail_bytes), True
        return None, True

    def _store_progress(self, lines: tuple[file_tail.CompleteLine, ...]) -> None:
        if lines:
            self._scan_position = str(lines[-1].position)
        if len(lines) < self.event_batch_size:
            self._complete = True

    def _idle_events(self, line: file_tail.CompleteLine) -> tuple[raw_event_models.RawEvent, ...]:
        try:
            record = transcript.parse_line(line.content.decode(TEXT_ENCODING))
        except (UnicodeDecodeError, ValidationError):
            return ()
        if not isinstance(record, transcript.TeammateIdleTranscriptRecord):
            return ()
        events = []
        for actor_id, index in self._notification_positions(record).items():
            events.append(self._idle_event(line, actor_id, index))
        return tuple(events)

    def _notification_positions(
        self,
        record: transcript.TeammateIdleTranscriptRecord,
    ) -> dict[domain_ids.ActorId, int]:
        positions: dict[domain_ids.ActorId, int] = {}
        for index, notification in enumerate(record.notifications):
            native_actor_id = transcript.teammate_actor_id(
                self.source_path,
                notification.from_,
            ) or claude_ids.ClaudeCodeActorId(notification.from_)
            positions[claude_ids.actor_id_from_claude_code(native_actor_id)] = index
        return positions

    def _idle_event(
        self,
        line: file_tail.CompleteLine,
        actor_id: domain_ids.ActorId,
        index: int,
    ) -> raw_event_models.RawEvent:
        return raw_event_models.RawEvent(
            raw_event_id=domain_ids.RawEventId(
                f"{self.source_identity}:{line.position}:idle:{index}",
            ),
            harness=HARNESS,
            source_type="transcript",
            source_name=self.source_path,
            source_position=str(line.position),
            session_id=self.context.session_id,
            actor_id=actor_id,
            parent_actor_id=self.context.lead_actor_id,
            observed_at=time.time(),
            encoding="jsonl",
            payload=line.content,
            source_identity=self.source_identity,
        )
