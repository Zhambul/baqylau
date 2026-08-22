"""Claude Code raw event discovery: transcript and task files, read as observations."""

from __future__ import annotations

import glob
import hashlib
import os
import time

from pydantic import ValidationError

from domain.ids import ActorId, HarnessName, RawEventId
from domain.values import ActorRole
from harness.contract import HarnessRawEventSource, HarnessRawEventSources
from harness.impl.claude_code import model
from harness.impl.claude_code.ids import (
    ClaudeCodeActorId,
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
        source_hash = hashlib.sha256(self.source_path.encode("utf-8")).hexdigest()
        self.source_identity = f"claude_code:transcript:{source_hash}"

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        raw_events: list[RawEvent] = []
        try:
            source = open(self.source_path, "rb")
        except FileNotFoundError:
            return ()
        with source:
            if after_position is not None:
                source.seek(int(after_position))
                skipped = source.readline()
                if not skipped.endswith(b"\n"):
                    return ()
            for _ in range(self.EVENT_BATCH_SIZE):
                line_position = source.tell()
                line = source.readline()
                if not line or not line.endswith(b"\n"):
                    break
                actor_id, parent_actor_id = self._actor_context(line)
                raw_events.append(RawEvent(
                    raw_event_id=RawEventId(f"{self.source_identity}:{line_position}"),
                    harness=HARNESS,
                    source_type=(f"{self.actor_role}_transcript" if self.actor_role else "transcript"),
                    source_name=self.source_path,
                    source_position=str(line_position),
                    session_id=self.context.session_id,
                    actor_id=actor_id,
                    parent_actor_id=parent_actor_id,
                    observed_at=time.time(),
                    encoding="jsonl",
                    payload=line,
                    source_identity=self.source_identity,
                ))
        return tuple(raw_events)

    def _actor_context(self, line: bytes) -> tuple[ActorId, ActorId | None]:
        try:
            record = transcript.parse_line(line.decode("utf-8"))
        except (UnicodeDecodeError, ValidationError):
            record = None
        if isinstance(record, transcript.TeamMessageTranscriptRecord):
            sender_text = record.sender
            if not sender_text:
                return self.context.actor_id, self.context.parent_actor_id
            # `team-lead` is the LEAD under its teammate-vocabulary alias, not a
            # participant of its own (transcript.LEAD_TEAMMATE_ID).
            sender = (
                self.context.lead_actor_id
                if sender_text == transcript.LEAD_TEAMMATE_ID
                else actor_id_from_claude_code(ClaudeCodeActorId(sender_text))
            )
            parent_actor_id = None if sender == self.context.lead_actor_id else self.context.lead_actor_id
            return sender, parent_actor_id
        if (
            isinstance(record, transcript.ActorAssignmentFinishedTranscriptRecord)
            and record.actor_id
        ):
            return actor_id_from_claude_code(record.actor_id), self.context.lead_actor_id
        return self.context.actor_id, self.context.parent_actor_id


class ClaudeTaskRawEventSource(HarnessRawEventSource):
    """Capture Claude Code's session task files as immutable raw observations.

    Position encoding: `list:<digest of the whole task snapshot>`, carried by the
    MEMBERSHIP event, which is therefore emitted last. When anything changed,
    every current task is emitted — unchanged ones carry their previous identity
    and deduplicate on record. Deletions need no synthetic record: the
    membership fact names the survivors and the projection prunes the rest.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        config_directory = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
        native_session_id = claude_code_session_id_from_domain(session.session_id)
        session_prefix = str(native_session_id).split("-", 1)[0]
        self.task_directory = os.path.join(config_directory, "tasks", f"session-{session_prefix}")
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


class ClaudeRawEventSources(HarnessRawEventSources):
    def for_session(self, session: Session) -> tuple[HarnessRawEventSource, ...]:
        if not transcript.owns(session.source_reference):
            return ()
        sources: list[HarnessRawEventSource] = [
            ClaudeTranscriptRawEventSource(session.source_context),
            ClaudeTaskRawEventSource(session),
        ]
        transcript_base = (
            session.source_reference[:-len(".jsonl")]
            if session.source_reference.endswith(".jsonl")
            else session.source_reference
        )
        child_pattern = os.path.join(transcript_base, transcript.AGENT_SUBDIR, "agent-*.jsonl")
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
        return tuple(sources)
