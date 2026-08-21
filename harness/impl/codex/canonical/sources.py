"""Codex raw event discovery: rollout files, read as observations."""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import time
from typing import Any, Literal

from domain.ids import ActorId, HarnessName, HarnessSessionId, RawEventId
from harness.contract import HarnessRawEventSource, HarnessRawEventSources
from harness.impl.codex.canonical import rollout
from harness.models import RawEvent, RawEventSourceContext, Session

HARNESS = HarnessName("codex")
ROLLOUT_NAME = re.compile(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(.+)\.jsonl$")
EVENT_BATCH_SIZE = 100


def harness_session_id(path: str) -> str:
    match = ROLLOUT_NAME.search(os.path.basename(path))
    return match.group(1) if match else os.path.splitext(os.path.basename(path))[0]


def session_metadata(path: str) -> dict[str, Any]:  # loose: codex JSON, wave 2 gives it a real shape
    try:
        with open(path, encoding="utf-8") as source:
            for _ in range(5):
                line = source.readline()
                if not line:
                    break
                document = json.loads(line)
                if document.get("type") == "session_meta":
                    return document.get("payload") or {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return {}


def _parent_thread_id(metadata: dict[str, Any]) -> str | None:  # loose: codex JSON, wave 2 gives it a real shape
    source = metadata.get("source")
    spawn = (
        ((source.get("subagent") or {}).get("thread_spawn") or {})
        if isinstance(source, dict)
        else {}
    )
    parent = spawn.get("parent_thread_id") or metadata.get("parent_thread_id")
    return str(parent).strip() if parent else None


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
    if not metadata:
        return False
    return metadata.get("thread_source") != "subagent" and not metadata.get("parent_thread_id")


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
        actor_relation: Literal["child", "sidecar"] | None = None,
    ) -> None:
        self.context = raw_event_source_context
        self.child_body_position = child_body_position
        self.actor_relation = actor_relation
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
            return f"{self.actor_relation}_replay"
        return f"{self.actor_relation}_rollout" if self.actor_relation else "rollout"


class CodexRawEventSources(HarnessRawEventSources):
    def __init__(self) -> None:
        self._known_rollout_paths: tuple[str, ...] = ()
        self._child_rollouts: dict[str, tuple[str, ...]] = {}
        self._next_child: dict[str, int] = {}

    def _next_child_rollout(self, parent_harness_session_id: HarnessSessionId) -> tuple[str, ...]:
        rollout_paths = _rollout_paths()
        if rollout_paths != self._known_rollout_paths:
            children: dict[str, list[str]] = {}
            for rollout_path in rollout_paths:
                parent_id = _parent_thread_id(session_metadata(rollout_path))
                if parent_id:
                    children.setdefault(parent_id, []).append(rollout_path)
            self._known_rollout_paths = rollout_paths
            self._child_rollouts = {
                parent_id: tuple(paths)
                for parent_id, paths in children.items()
            }
        parent_id_text = str(parent_harness_session_id)
        child_rollouts = self._child_rollouts.get(parent_id_text, ())
        if not child_rollouts:
            return ()
        position = self._next_child.get(parent_id_text, 0) % len(child_rollouts)
        self._next_child[parent_id_text] = position + 1
        return (child_rollouts[position],)

    def for_session(self, session: Session) -> tuple[HarnessRawEventSource, ...]:
        sources: list[HarnessRawEventSource] = []
        owns_lead_session = lead_rollout(session.source_reference)
        if owns_lead_session:
            sources.append(CodexRolloutRawEventSource(session.source_context))
        for child_path in self._next_child_rollout(session.harness_session_id):
            child_body_position = rollout.subagent_body_offset(child_path)
            if child_body_position == 0:
                continue
            sources.append(
                CodexRolloutRawEventSource(
                    RawEventSourceContext(
                        session_id=session.session_id,
                        lead_actor_id=session.lead_actor_id,
                        actor_id=ActorId(harness_session_id(child_path)),
                        parent_actor_id=session.lead_actor_id,
                        source_reference=child_path,
                    ),
                    child_body_position,
                    "child" if owns_lead_session else "sidecar",
                )
            )
        return tuple(sources)
