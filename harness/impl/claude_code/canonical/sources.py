"""Claude Code raw event discovery: transcript and task files, read as observations."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import time
from typing import Literal

from domain.ids import ActorId, RawEventId
from harness.contract import HarnessRawEventSource, HarnessRawEventSources
from harness.impl.claude_code import model
from harness.impl.claude_code.canonical import transcript
from harness.models import RawEvent, RawEventSourceContext, Session


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
        actor_role: Literal["child", "teammate"] | None = None,
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
                    harness="claude_code",
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
        except (UnicodeDecodeError, json.JSONDecodeError):
            record = None
        if record and record.get("kind") == "teammsg":
            sender_text = str(record.get("sender") or "")
            if not sender_text:
                return self.context.actor_id, self.context.parent_actor_id
            # `team-lead` is the LEAD under its teammate-vocabulary alias, not a
            # participant of its own (transcript.LEAD_TEAMMATE_ID).
            sender = (
                self.context.lead_actor_id
                if sender_text == transcript.LEAD_TEAMMATE_ID
                else ActorId(sender_text)
            )
            parent_actor_id = None if sender == self.context.lead_actor_id else self.context.lead_actor_id
            return sender, parent_actor_id
        if record and record.get("kind") == "actor_assignment_finished" and record.get("actor_id"):
            return ActorId(str(record["actor_id"])), self.context.lead_actor_id
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
        session_prefix = session.harness_session_id.split("-", 1)[0]
        self.task_directory = os.path.join(config_directory, "tasks", f"session-{session_prefix}")
        self.source_identity = f"claude_code:tasks:{session.harness_session_id}"

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        current = {}
        for path in sorted(glob.glob(os.path.join(self.task_directory, "*.json"))):
            try:
                with open(path, encoding="utf-8") as source:
                    task = json.load(source)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(task, dict) and task.get("id") is not None:
                current[str(task["id"])] = task
        if not current and after_position is None:
            return ()
        snapshot = json.dumps(current, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        snapshot_digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
        position = f"list:{snapshot_digest}"
        if position == after_position:
            return ()
        raw_events = []
        for task in current.values():
            encoded = json.dumps(task, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            raw_events.append(RawEvent(
                raw_event_id=RawEventId(f"{self.source_identity}:{task['id']}:{digest}"),
                harness="claude_code",
                source_type="tasks",
                source_name=self.task_directory,
                source_position=f"{task['id']}:{digest}",
                session_id=self.session.session_id,
                actor_id=self.session.lead_actor_id,
                parent_actor_id=None,
                observed_at=time.time(),
                encoding="json",
                payload=encoded.encode("utf-8"),
                source_identity=self.source_identity,
            ))
        membership = json.dumps(
            {"list_id": "session", "task_ids": list(current)},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        # The raw identity chains from the previous position so that returning to
        # an EARLIER snapshot still records a new observation (a bare digest would
        # deduplicate against the old row and the position could never latch);
        # the canonical fact still converges on the snapshot itself.
        revision = hashlib.sha256(f"{after_position or ''}::{snapshot_digest}".encode("utf-8")).hexdigest()
        raw_events.append(RawEvent(
            raw_event_id=RawEventId(f"{self.source_identity}:list:{revision}"),
            harness="claude_code",
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
                        actor_id=ActorId(actor_name),
                        parent_actor_id=session.lead_actor_id,
                        source_reference=child_path,
                    ),
                    (
                        "teammate"
                        if model.agent_meta(session.source_reference, ActorId(actor_name)).get("taskKind")
                        == "in_process_teammate"
                        else "child"
                    ),
                )
            )
        return tuple(sources)
