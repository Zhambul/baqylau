"""Codex raw event discovery: rollout files, read as observations."""

from __future__ import annotations

import glob
import hashlib
import os
import re
import time
from dataclasses import dataclass
from pydantic import ValidationError
from domain.ids import HarnessName, RawEventId
from harness.impl.codex.ids import (
    CodexActorId,
    CodexSessionId,
    actor_id_from_codex,
    codex_session_id_from_domain,
)
from domain.values import ActorRole
from harness.contract import HarnessRawEventSource, HarnessRawEventSources
from harness.impl.codex.canonical import rollout
from harness.impl.codex.canonical.records import (
    RolloutDocument,
    RolloutHeader,
    SessionMetaPayload,
    SessionMetaSource,
)
from harness.models import RawEvent, RawEventSourceContext, Session

HARNESS = HarnessName.CODEX
ROLLOUT_NAME = re.compile(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(.+)\.jsonl$")
EVENT_BATCH_SIZE = 100


def codex_session_id(path: str) -> CodexSessionId:
    match = ROLLOUT_NAME.search(os.path.basename(path))
    return CodexSessionId(
        match.group(1) if match else os.path.splitext(os.path.basename(path))[0]
    )


def session_metadata(path: str) -> SessionMetaPayload | None:
    """The rollout's own `session_meta` record, DECLARED (records.py) and
    validated — None when the file has none in its first few lines (a
    fresh/atypical rollout) or can't be read, distinct from a session_meta
    that validated to an entirely-default payload (lead_rollout tells them
    apart: no metadata at all is never a lead rollout)."""
    try:
        with open(path, encoding="utf-8") as source:
            for _ in range(5):
                line = source.readline()
                if not line:
                    break
                header = RolloutHeader.model_validate_json(line)
                if header.type == "session_meta":
                    return RolloutDocument[SessionMetaPayload].model_validate_json(line).payload
    except (OSError, UnicodeDecodeError, ValidationError):
        return None
    return None


def _parent_thread_id(session_meta_payload: SessionMetaPayload | None) -> str | None:
    if session_meta_payload is None:
        return None
    source = (
        session_meta_payload.source
        if isinstance(session_meta_payload.source, SessionMetaSource) else None
    )
    spawn = source.subagent.thread_spawn if source and source.subagent else None
    parent = (spawn.parent_thread_id if spawn else None) or session_meta_payload.parent_thread_id
    return parent.strip() if parent else None


def _rollout_paths() -> tuple[str, ...]:
    codex_home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    pattern = os.path.join(codex_home, "sessions", "*", "*", "*", "rollout-*.jsonl")
    return tuple(sorted(glob.glob(pattern)))


def lead_rollout(path: str) -> bool:
    """Whether the path names a LEAD rollout — subagent rollouts and
    non-rollouts announce no session of their own."""
    path = os.path.realpath(path)
    if not os.path.isfile(path) or not ROLLOUT_NAME.search(os.path.basename(path)):
        return False
    metadata = session_metadata(path)
    if metadata is None:
        return False
    return metadata.thread_source != "subagent" and not metadata.parent_thread_id


class CodexRolloutRawEventSource(HarnessRawEventSource):
    """One rollout file, read as complete lines.

    Position encoding: the byte offset where the last emitted line STARTS (the
    translator keys on it — `source_position == "0"` marks the opening
    session_meta, and the collaboration backscan reads everything BEFORE it).
    Resuming therefore seeks to it and skips one line.
    """

    def __init__(
        self,
        raw_event_source_context: RawEventSourceContext,
        child_body_position: int | None = None,
        actor_role: ActorRole | None = None,
    ) -> None:
        self.context = raw_event_source_context
        self.child_body_position = child_body_position
        self.actor_role = actor_role
        self.source_path = os.path.realpath(raw_event_source_context.source_reference)
        source_hash = hashlib.sha256(self.source_path.encode("utf-8")).hexdigest()
        self.source_identity = f"codex:rollout:{source_hash}"

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
            for _ in range(EVENT_BATCH_SIZE):
                line_position = source.tell()
                line = source.readline()
                if not line or not line.endswith(b"\n"):
                    break
                raw_events.append(RawEvent(
                    raw_event_id=RawEventId(f"{self.source_identity}:{line_position}"),
                    harness=HARNESS,
                    source_type=self._source_type(line_position),
                    source_name=self.source_path,
                    source_position=str(line_position),
                    session_id=self.context.session_id,
                    actor_id=self.context.actor_id,
                    parent_actor_id=self.context.parent_actor_id,
                    observed_at=time.time(),
                    encoding="jsonl",
                    payload=line,
                    source_identity=self.source_identity,
                ))
        return tuple(raw_events)

    def _source_type(self, line_position: int) -> str:
        if (
            self.child_body_position is not None
            and 0 < line_position < self.child_body_position
        ):
            return f"{self.actor_role}_replay"
        return f"{self.actor_role}_rollout" if self.actor_role else "rollout"


@dataclass
class ChildRollouts:
    parent_session_id: CodexSessionId
    paths: tuple[str, ...]
    next_index: int = 0


class CodexRawEventSources(HarnessRawEventSources):
    def __init__(self) -> None:
        self._known_rollout_paths: tuple[str, ...] = ()
        self._child_rollouts: list[ChildRollouts] = []

    def _next_child_rollout(self, parent_codex_session_id: CodexSessionId) -> tuple[str, ...]:
        rollout_paths = _rollout_paths()
        if rollout_paths != self._known_rollout_paths:
            children: list[ChildRollouts] = []
            for rollout_path in rollout_paths:
                parent_id = _parent_thread_id(session_metadata(rollout_path))
                if parent_id:
                    group = next(
                        (child for child in children if child.parent_session_id == parent_id),
                        None,
                    )
                    if group is None:
                        group = ChildRollouts(CodexSessionId(parent_id), ())
                        children.append(group)
                    group.paths = (*group.paths, rollout_path)
            self._known_rollout_paths = rollout_paths
            self._child_rollouts = children
        selected_children = next(
            (child for child in self._child_rollouts if child.parent_session_id == parent_codex_session_id),
            None,
        )
        if selected_children is None or not selected_children.paths:
            return ()
        position = selected_children.next_index % len(selected_children.paths)
        selected_children.next_index += 1
        return (selected_children.paths[position],)

    def for_session(self, session: Session) -> tuple[HarnessRawEventSource, ...]:
        sources: list[HarnessRawEventSource] = []
        owns_lead_session = lead_rollout(session.source_reference)
        if owns_lead_session:
            sources.append(CodexRolloutRawEventSource(session.source_context))
        for child_path in self._next_child_rollout(
            codex_session_id_from_domain(session.session_id)
        ):
            child_body_position = rollout.subagent_body_offset(child_path)
            if child_body_position == 0:
                continue
            sources.append(
                CodexRolloutRawEventSource(
                    RawEventSourceContext(
                        session_id=session.session_id,
                        lead_actor_id=session.lead_actor_id,
                        actor_id=actor_id_from_codex(
                            CodexActorId(codex_session_id(child_path))
                        ),
                        parent_actor_id=session.lead_actor_id,
                        source_reference=child_path,
                    ),
                    child_body_position,
                    ActorRole.CHILD if owns_lead_session else ActorRole.SIDECAR,
                )
            )
        return tuple(sources)
