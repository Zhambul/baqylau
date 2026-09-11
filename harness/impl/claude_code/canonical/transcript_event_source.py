# Copyright (c) 2026 Zhambyl Yermagambet
"""Read Claude Code transcript events."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from pydantic import ValidationError

from domain import ids as domain_ids
from harness import contract as harness_contract, file_tail
from harness.impl.claude_code import ids as claude_ids
from harness.impl.claude_code.canonical import transcript, transcript_sources
from harness.models import raw_events as raw_event_models

HARNESS = domain_ids.HarnessName("claude_code")
TEXT_ENCODING = "utf-8"
TRANSCRIPT_SUFFIX = ".jsonl"


class ClaudeTranscriptRawEventSource(harness_contract.HarnessRawEventSource):
    """Read one transcript file as complete lines."""

    event_batch_size = 100

    def __init__(
        self,
        raw_event_source_context: raw_event_models.RawEventSourceContext,
        actor_role: str | None = None,
    ) -> None:
        """Initialize the transcript source."""
        self.context = raw_event_source_context
        self.actor_role = actor_role
        self.source_path = os.path.realpath(raw_event_source_context.source_reference)
        self.tail = file_tail.CompleteLineTail(self.source_path)
        source_hash = hashlib.sha256(self.source_path.encode(TEXT_ENCODING)).hexdigest()
        self.source_identity = f"claude_code:transcript:{source_hash}"

    def watch_paths(self) -> tuple[str, ...]:
        """List the transcript file.

        Returns:
            The transcript path.

        """
        return (self.source_path,)

    def read(self, after_position: str | None) -> tuple[raw_event_models.RawEvent, ...]:
        """Return read.

        Returns:
            Read.

        """
        raw_events: list[raw_event_models.RawEvent] = []
        for line in self.tail.read(after_position, self.event_batch_size):
            contexts = self._actor_contexts(line.content)
            for index, actor_context in enumerate(contexts):
                raw_events.append(self._raw_event(line, actor_context, index, len(contexts)))
        return tuple(raw_events)

    def _raw_event(
        self,
        line: file_tail.CompleteLine,
        actor_context: transcript_sources.ActorContext,
        context_index: int,
        context_count: int,
    ) -> raw_event_models.RawEvent:
        identity_suffix = f":idle:{context_index}" if context_count > 1 else ""
        return raw_event_models.RawEvent(
            raw_event_id=domain_ids.RawEventId(
                f"{self.source_identity}:{line.position}{identity_suffix}",
            ),
            harness=HARNESS,
            source_type=(f"{self.actor_role}_transcript" if self.actor_role else "transcript"),
            source_name=self.source_path,
            source_position=str(line.position),
            session_id=self.context.session_id,
            actor_id=actor_context.actor_id,
            parent_actor_id=actor_context.parent_actor_id,
            observed_at=time.time(),
            encoding="jsonl",
            payload=line.content,
            source_identity=self.source_identity,
        )

    def _actor_contexts(self, line: bytes) -> tuple[transcript_sources.ActorContext, ...]:
        try:
            record = transcript.parse_line(line.decode(TEXT_ENCODING))
        except (UnicodeDecodeError, ValidationError):
            record = None
        if isinstance(record, transcript.TeammateIdleTranscriptRecord):
            contexts = []
            for notification in record.notifications:
                native_actor_id = transcript.teammate_actor_id(
                    self.source_path,
                    notification.from_,
                ) or claude_ids.ClaudeCodeActorId(notification.from_)
                context = transcript_sources.ActorContext(
                    claude_ids.actor_id_from_claude_code(native_actor_id),
                    self.context.lead_actor_id,
                )
                if context not in contexts:
                    contexts.append(context)
            if contexts:
                return tuple(contexts)
        return (self._actor_context(line, record=record),)

    def _actor_context(
        self,
        line: bytes,
        *,
        record: transcript.TranscriptRecord | None = None,
    ) -> transcript_sources.ActorContext:
        if record is None:
            record = transcript_sources.transcript_record(line)
        if isinstance(record, transcript.TeamMessageTranscriptRecord):
            return transcript_sources.team_message_context(record, self.context, self.source_path)
        if isinstance(record, transcript.ActorAssignmentFinishedTranscriptRecord) and record.actor_id:
            return transcript_sources.ActorContext(
                claude_ids.actor_id_from_claude_code(record.actor_id),
                self.context.lead_actor_id,
            )
        if isinstance(record, transcript.BackgroundCommandCompletedTranscriptRecord):
            owner = self._child_tool_owner(record.operation_id)
            if owner is not None:
                return transcript_sources.ActorContext(owner, self.context.lead_actor_id)
        return transcript_sources.ActorContext(
            self.context.actor_id,
            self.context.parent_actor_id,
        )

    def _child_tool_owner(self, call_id: claude_ids.ClaudeCodeCallId) -> domain_ids.ActorId | None:
        if not call_id or self.context.parent_actor_id is not None:
            return None
        transcript_base = self.source_path.removesuffix(TRANSCRIPT_SUFFIX)
        child_directory = Path(transcript_base) / transcript.AGENT_SUBDIR
        owners: list[domain_ids.ActorId] = []
        for child_path in sorted(child_directory.glob("agent-*.jsonl")):
            if not transcript_sources.transcript_has_tool_call(child_path, call_id):
                continue
            actor_id = transcript_sources.child_actor_id(child_path)
            if actor_id is not None:
                owners.append(actor_id)
        return transcript_sources.unique_child_owner(owners, call_id)
