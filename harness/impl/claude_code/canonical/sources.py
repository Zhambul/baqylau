"""Claude Code raw event discovery: transcript and task files, read as observations."""

from __future__ import annotations

import glob
import hashlib
import os
import time
from dataclasses import dataclass

from pydantic import ValidationError

from domain.ids import ActorId, HarnessName, RawEventId, SessionId
from domain.values import ActorRole
from harness.contract import HarnessRawEventSource, HarnessRawEventSources
from harness.file_tail import CompleteLineTail
from harness.impl.claude_code import model
from harness.impl.claude_code.ids import (
    ClaudeCodeActorId,
    ClaudeCodeCallId,
    actor_id_from_claude_code,
    claude_code_session_id_from_domain,
    ClaudeCodeTaskListId,
)
from harness.impl.claude_code.canonical import records, transcript
from harness.models import RawEvent, RawEventSourceContext, Session

HARNESS = HarnessName.CLAUDE_CODE


class ClaudeTranscriptRawEventSource(HarnessRawEventSource):
    """One transcript file, read as complete lines.

    Position encoding: the byte offset where the last emitted line STARTS (the
    translator keys on it — `source_position == "0"` marks a record that opens
    its transcript). Resuming therefore seeks to it and skips one line.
    """

    EVENT_BATCH_SIZE = 100

    def __init__(
        self,
        raw_event_source_context: RawEventSourceContext,
        actor_role: ActorRole | None = None,
    ) -> None:
        self.context = raw_event_source_context
        self.actor_role = actor_role
        self.source_path = os.path.realpath(raw_event_source_context.source_reference)
        self.tail = CompleteLineTail(self.source_path)
        source_hash = hashlib.sha256(self.source_path.encode("utf-8")).hexdigest()
        self.source_identity = f"claude_code:transcript:{source_hash}"

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        raw_events: list[RawEvent] = []
        for line in self.tail.read(after_position, self.EVENT_BATCH_SIZE):
            contexts = self._actor_contexts(line.content)
            for index, (actor_id, parent_actor_id) in enumerate(contexts):
                identity_suffix = f":idle:{index}" if len(contexts) > 1 else ""
                raw_events.append(RawEvent(
                    raw_event_id=RawEventId(
                        f"{self.source_identity}:{line.position}{identity_suffix}"
                    ),
                    harness=HARNESS,
                    source_type=(f"{self.actor_role}_transcript" if self.actor_role else "transcript"),
                    source_name=self.source_path,
                    source_position=str(line.position),
                    session_id=self.context.session_id,
                    actor_id=actor_id,
                    parent_actor_id=parent_actor_id,
                    observed_at=time.time(),
                    encoding="jsonl",
                    payload=line.content,
                    source_identity=self.source_identity,
                ))
        return tuple(raw_events)

    def _actor_contexts(self, line: bytes) -> tuple[tuple[ActorId, ActorId | None], ...]:
        try:
            record = transcript.parse_line(line.decode("utf-8"))
        except (UnicodeDecodeError, ValidationError):
            record = None
        if isinstance(record, transcript.TeammateIdleTranscriptRecord):
            contexts = []
            for notification in record.notifications:
                native_actor_id = (
                    transcript.teammate_actor_id(self.source_path, notification.from_)
                    or ClaudeCodeActorId(notification.from_)
                )
                context = (
                    actor_id_from_claude_code(native_actor_id),
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
    ) -> tuple[ActorId, ActorId | None]:
        if record is None:
            try:
                record = transcript.parse_line(line.decode("utf-8"))
            except (UnicodeDecodeError, ValidationError):
                record = None
        if isinstance(record, transcript.TeamMessageTranscriptRecord):
            sender_text = record.sender
            if not sender_text:
                return self.context.actor_id, self.context.parent_actor_id
            if (
                sender_text == transcript.LEAD_TEAMMATE_ID
                and self.context.parent_actor_id is not None
            ):
                return self.context.actor_id, self.context.parent_actor_id
            # `team-lead` is the LEAD under its teammate-vocabulary alias, not a
            # participant of its own (transcript.LEAD_TEAMMATE_ID).
            sender = (
                self.context.lead_actor_id
                if sender_text == transcript.LEAD_TEAMMATE_ID
                else actor_id_from_claude_code(
                    transcript.teammate_actor_id(self.source_path, sender_text)
                    or ClaudeCodeActorId(sender_text)
                )
            )
            parent_actor_id = None if sender == self.context.lead_actor_id else self.context.lead_actor_id
            return sender, parent_actor_id
        if (
            isinstance(record, transcript.ActorAssignmentFinishedTranscriptRecord)
            and record.actor_id
        ):
            return actor_id_from_claude_code(record.actor_id), self.context.lead_actor_id
        if isinstance(record, transcript.BackgroundCommandCompletedTranscriptRecord):
            owner = self._child_tool_owner(record.operation_id)
            if owner is not None:
                return owner, self.context.lead_actor_id
        return self.context.actor_id, self.context.parent_actor_id

    def _child_tool_owner(self, call_id: ClaudeCodeCallId) -> ActorId | None:
        """Find the child transcript that contains one exact native tool call.

        Claude Code writes a child's background completion to the parent queue
        without a child id. The tool-use id is unchanged. Child transcript
        files are the durable relation from that id to the actor, including
        after a Baqylau restart.
        """
        if not call_id or self.context.parent_actor_id is not None:
            return None
        transcript_base = (
            self.source_path[:-len(".jsonl")]
            if self.source_path.endswith(".jsonl")
            else self.source_path
        )
        child_pattern = os.path.join(
            transcript_base,
            transcript.AGENT_SUBDIR,
            "agent-*.jsonl",
        )
        owners: list[ActorId] = []
        for child_path in sorted(glob.glob(child_pattern)):
            if not _transcript_has_tool_call(child_path, call_id):
                continue
            filename = os.path.basename(child_path)
            actor_name = filename[len("agent-"):-len(".jsonl")]
            if actor_name:
                owners.append(
                    actor_id_from_claude_code(ClaudeCodeActorId(actor_name))
                )
        if len(owners) > 1:
            raise ValueError(
                f"Claude Code tool call {call_id!r} belongs to multiple child transcripts"
            )
        return owners[0] if owners else None


class ClaudeTeammateIdleRawEventSource(HarnessRawEventSource):
    """Reconcile every durable Claude team completion, including old ones.

    This source stores only ``idle_notification`` lines. Its separate identity
    lets a new Baqylau version recover completion facts that an older
    transcript reader ignored, without copying a complete transcript into the
    raw-event store again.
    """

    EVENT_BATCH_SIZE = 500
    RECONCILE_TAIL_BYTES = 1_000_000

    def __init__(self, raw_event_source_context: RawEventSourceContext) -> None:
        self.context = raw_event_source_context
        self.source_path = os.path.realpath(raw_event_source_context.source_reference)
        self.tail = CompleteLineTail(self.source_path)
        source_hash = hashlib.sha256(self.source_path.encode("utf-8")).hexdigest()
        self.source_identity = f"claude_code:teammate_idle:{source_hash}"
        self._scan_position: str | None = None
        self._complete = False

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        if self._complete:
            return ()
        start = self._scan_position if self._scan_position is not None else after_position
        if start is None:
            try:
                size = os.path.getsize(self.source_path)
            except OSError:
                self._complete = True
                return ()
            if size > self.RECONCILE_TAIL_BYTES:
                start = str(size - self.RECONCILE_TAIL_BYTES)
        lines = self.tail.read(start, self.EVENT_BATCH_SIZE)
        if lines:
            self._scan_position = str(lines[-1].position)
        if len(lines) < self.EVENT_BATCH_SIZE:
            self._complete = True
        raw_events = []
        for line in lines:
            try:
                record = transcript.parse_line(line.content.decode("utf-8"))
            except (UnicodeDecodeError, ValidationError):
                continue
            if not isinstance(record, transcript.TeammateIdleTranscriptRecord):
                continue
            notifications_by_actor: dict[
                ActorId,
                tuple[int, ClaudeCodeActorId],
            ] = {}
            for index, notification in enumerate(record.notifications):
                native_actor_id = (
                    transcript.teammate_actor_id(self.source_path, notification.from_)
                    or ClaudeCodeActorId(notification.from_)
                )
                notifications_by_actor[actor_id_from_claude_code(native_actor_id)] = (
                    index,
                    native_actor_id,
                )
            for actor_id, (index, _native_actor_id) in notifications_by_actor.items():
                raw_events.append(RawEvent(
                    raw_event_id=RawEventId(
                        f"{self.source_identity}:{line.position}:idle:{index}"
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
                ))
        return tuple(raw_events)


def _transcript_has_tool_call(path: str, call_id: ClaudeCodeCallId) -> bool:
    try:
        source = open(path, "rb")
    except OSError:
        return False
    with source:
        for line in source:
            if str(call_id).encode("utf-8") not in line:
                continue
            try:
                assistant = records.AssistantRecord.model_validate_json(line)
            except ValidationError:
                continue
            content = assistant.message.content if assistant.message is not None else None
            if isinstance(content, list) and any(
                isinstance(block, records.ToolUseBlock) and block.id == call_id
                for block in content
            ):
                return True
    return False


class ClaudeTaskRawEventSource(HarnessRawEventSource):
    """Capture Claude Code's session task files as immutable raw observations.

    Position encoding: `list:<digest of the whole task snapshot>`, carried by the
    MEMBERSHIP event, which is therefore emitted last. When anything changed,
    every current task is emitted — unchanged ones carry their previous identity
    and deduplicate on record. Deletions need no synthetic record: the
    membership fact names the survivors and the projection prunes the rest.
    """

    def __init__(self, session: Session, configuration_directory: str) -> None:
        self.session = session
        native_session_id = claude_code_session_id_from_domain(session.session_id)
        session_prefix = str(native_session_id).split("-", 1)[0]
        self.task_directory = os.path.join(
            configuration_directory,
            "tasks",
            f"session-{session_prefix}",
        )
        self.source_identity = f"claude_code:tasks:{session.session_id}"

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        current: list[records.TaskFile] = []
        for path in sorted(glob.glob(os.path.join(self.task_directory, "*.json"))):
            try:
                with open(path, encoding="utf-8") as source:
                    task = records.TaskFile.model_validate_json(source.read())
            except (OSError, UnicodeDecodeError):
                continue
            if task.id is not None:
                current.append(task)
        if not current and after_position is None:
            return ()
        snapshot = records.TaskSnapshot(tuple(current)).model_dump_json(exclude_none=True)
        snapshot_digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
        position = f"list:{snapshot_digest}"
        if position == after_position:
            return ()
        raw_events = []
        for task in current:
            encoded = task.model_dump_json(exclude_none=True)
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            raw_events.append(RawEvent(
                raw_event_id=RawEventId(f"{self.source_identity}:{task.id}:{digest}"),
                harness=HARNESS,
                source_type="tasks",
                source_name=self.task_directory,
                source_position=f"{task.id}:{digest}",
                session_id=self.session.session_id,
                actor_id=self.session.lead_actor_id,
                parent_actor_id=None,
                observed_at=time.time(),
                encoding="json",
                payload=encoded.encode("utf-8"),
                source_identity=self.source_identity,
            ))
        membership = records.TaskListDocument(
            list_id=ClaudeCodeTaskListId("session"),
            task_ids=[str(task.id) for task in current],
        ).model_dump_json(exclude_none=True)
        # The raw identity chains from the previous position so that returning to
        # an EARLIER snapshot still records a new observation (a bare digest would
        # deduplicate against the old row and the position could never latch);
        # the canonical fact still converges on the snapshot itself.
        revision = hashlib.sha256(f"{after_position or ''}::{snapshot_digest}".encode("utf-8")).hexdigest()
        raw_events.append(RawEvent(
            raw_event_id=RawEventId(f"{self.source_identity}:list:{revision}"),
            harness=HARNESS,
            source_type="task_list",
            source_name=self.task_directory,
            source_position=position,
            session_id=self.session.session_id,
            actor_id=self.session.lead_actor_id,
            parent_actor_id=None,
            observed_at=time.time(),
            encoding="json",
            payload=membership.encode("utf-8"),
            source_identity=self.source_identity,
        ))
        return tuple(raw_events)


@dataclass(frozen=True)
class ClaudeSessionSources:
    session_id: SessionId
    source_reference: str
    config_directory: str
    child_directory_marker: tuple[int, int] | None
    sources: tuple[HarnessRawEventSource, ...]


class ClaudeRawEventSources(HarnessRawEventSources):
    def __init__(self, configuration_directory: str) -> None:
        self.configuration_directory = configuration_directory
        self._sessions: list[ClaudeSessionSources] = []

    def release_session(self, session_id: SessionId) -> None:
        """Release transcript readers for one finished session."""
        self._sessions = [
            cached for cached in self._sessions if cached.session_id != session_id
        ]

    def for_session(self, session: Session) -> tuple[HarnessRawEventSource, ...]:
        if not transcript.owns(session.source_reference):
            return ()
        config_directory = self.configuration_directory
        transcript_base = (
            session.source_reference[:-len(".jsonl")]
            if session.source_reference.endswith(".jsonl")
            else session.source_reference
        )
        child_directory = os.path.join(transcript_base, transcript.AGENT_SUBDIR)
        child_directory_marker = _directory_marker(child_directory)
        previous = next(
            (
                item
                for item in self._sessions
                if item.session_id == session.session_id
                and item.source_reference == session.source_reference
                and item.config_directory == config_directory
            ),
            None,
        )
        if (
            previous is not None
            and previous.child_directory_marker == child_directory_marker
        ):
            return previous.sources
        sources: list[HarnessRawEventSource] = [
            ClaudeTranscriptRawEventSource(session.source_context),
            ClaudeTeammateIdleRawEventSource(session.source_context),
            ClaudeTaskRawEventSource(session, config_directory),
        ]
        child_pattern = os.path.join(child_directory, "agent-*.jsonl")
        for child_path in sorted(glob.glob(child_pattern)):
            filename = os.path.basename(child_path)
            actor_name = filename[len("agent-"):-len(".jsonl")]
            if not actor_name:
                continue
            sources.append(
                ClaudeTranscriptRawEventSource(
                    RawEventSourceContext(
                        session_id=session.session_id,
                        lead_actor_id=session.lead_actor_id,
                        actor_id=actor_id_from_claude_code(ClaudeCodeActorId(actor_name)),
                        parent_actor_id=session.lead_actor_id,
                        source_reference=child_path,
                    ),
                    (
                        ActorRole.TEAMMATE
                        if model.agent_meta(
                            session.source_reference,
                            actor_id_from_claude_code(ClaudeCodeActorId(actor_name)),
                        ).taskKind
                        == "in_process_teammate"
                        else ActorRole.CHILD
                    ),
                )
            )
        result = tuple(sources)
        if previous is not None:
            self._sessions.remove(previous)
        self._sessions.append(
            ClaudeSessionSources(
                session.session_id,
                session.source_reference,
                config_directory,
                child_directory_marker,
                result,
            )
        )
        return result


def _directory_marker(directory: str) -> tuple[int, int] | None:
    try:
        status = os.stat(directory)
    except OSError:
        return None
    return status.st_ino, status.st_mtime_ns
