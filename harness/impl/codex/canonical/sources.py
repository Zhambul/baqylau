"""Codex raw event discovery: rollout files, read as observations."""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pydantic import ValidationError
from domain.ids import HarnessName, RawEventId, SessionId
from harness.impl.codex.ids import (
    CodexActorId,
    CodexSessionId,
    actor_id_from_codex,
    codex_session_id_from_domain,
)
from domain.values import ActorRole
from harness.contract import HarnessRawEventSource, HarnessRawEventSources
from harness.file_tail import CompleteLineTail
from harness.impl.codex.canonical import rollout
from harness.impl.codex.canonical import title as native_title
from harness.impl.codex.canonical.records import (
    RolloutDocument,
    RolloutHeader,
    SessionMetaPayload,
    SessionMetaSource,
)
from harness.models import (
    TITLE_SOURCE_TYPE,
    RawEvent,
    RawEventSourceContext,
    Session,
)
from harness.models.directives import NativeTitleObservation
from repository.mapper.documents import encode_document

HARNESS = HarnessName.CODEX
ROLLOUT_NAME = re.compile(r"rollout-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}-(.+)\.jsonl$")
EVENT_BATCH_SIZE = 100
CATALOG_REFRESH_SECONDS = 1.0
# A source-identity revision is a data migration. Version 4 re-observes live
# rollouts so browser completions that version 3 classified as tool plumbing
# can add their missing browser facts and feed entries.
# Stable canonical identities deduplicate every fact that was already correct.
ROLLOUT_OBSERVATION_VERSION = 4


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


@dataclass(frozen=True)
class DirectorySnapshot:
    marker: tuple[int, int]
    entries: tuple[str, ...]


class RolloutCatalog:
    """Find new rollout files without reading every date directory each tick."""

    def __init__(self) -> None:
        self._root = ""
        self._directories: dict[str, DirectorySnapshot] = {}
        self._rollouts: dict[str, DirectorySnapshot] = {}

    def paths(self) -> tuple[str, ...]:
        root = os.path.join(
            os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"),
            "sessions",
        )
        if root != self._root:
            self._root = root
            self._directories.clear()
            self._rollouts.clear()
        years = self._subdirectories(root, 4)
        months = tuple(
            month
            for year in years
            for month in self._subdirectories(year, 2)
        )
        days = tuple(
            day
            for month in months
            for day in self._subdirectories(month, 2)
        )
        return tuple(
            rollout_path
            for day in days
            for rollout_path in self._rollout_files(day)
        )

    def _subdirectories(self, directory: str, name_width: int) -> tuple[str, ...]:
        marker = self._marker(directory)
        if marker is None:
            return ()
        previous = self._directories.get(directory)
        if previous is not None and previous.marker == marker:
            return previous.entries
        try:
            with os.scandir(directory) as entries:
                found = tuple(sorted(
                    entry.path
                    for entry in entries
                    if len(entry.name) == name_width
                    and entry.name.isdigit()
                    and entry.is_dir(follow_symlinks=False)
                ))
        except OSError:
            return ()
        self._directories[directory] = DirectorySnapshot(marker, found)
        return found

    def _rollout_files(self, directory: str) -> tuple[str, ...]:
        marker = self._marker(directory)
        if marker is None:
            return ()
        previous = self._rollouts.get(directory)
        if previous is not None and previous.marker == marker:
            return previous.entries
        try:
            with os.scandir(directory) as entries:
                found = tuple(sorted(
                    entry.path
                    for entry in entries
                    if ROLLOUT_NAME.search(entry.name)
                    and entry.is_file(follow_symlinks=False)
                ))
        except OSError:
            return ()
        self._rollouts[directory] = DirectorySnapshot(marker, found)
        return found

    @staticmethod
    def _marker(directory: str) -> tuple[int, int] | None:
        try:
            status = os.stat(directory)
        except OSError:
            return None
        return status.st_ino, status.st_mtime_ns


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
        self.tail = CompleteLineTail(self.source_path)
        source_hash = hashlib.sha256(self.source_path.encode("utf-8")).hexdigest()
        self.source_identity = (
            f"codex:rollout:v{ROLLOUT_OBSERVATION_VERSION}:{source_hash}"
        )

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        raw_events: list[RawEvent] = []
        for line in self.tail.read(after_position, EVENT_BATCH_SIZE):
            raw_events.append(RawEvent(
                raw_event_id=RawEventId(f"{self.source_identity}:{line.position}"),
                harness=HARNESS,
                source_type=self._source_type(line.position),
                source_name=self.source_path,
                source_position=str(line.position),
                session_id=self.context.session_id,
                actor_id=self.context.actor_id,
                parent_actor_id=self.context.parent_actor_id,
                observed_at=time.time(),
                encoding="jsonl",
                payload=line.content,
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


class CodexTitleRawEventSource(HarnessRawEventSource):
    """Observe the native Codex index, which has no title event stream."""

    def __init__(self, raw_event_source_context: RawEventSourceContext) -> None:
        self.context = raw_event_source_context
        self._checked_store = False
        self._store_marker: native_title.CodexTitleStoreMarker | None = None
        source_hash = hashlib.sha256(
            os.path.realpath(raw_event_source_context.source_reference).encode("utf-8")
        ).hexdigest()
        self.source_identity = f"codex:title:{source_hash}"

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        store_marker = native_title.title_store_marker(self.context.source_reference)
        if (
            store_marker is not None
            and self._checked_store
            and store_marker == self._store_marker
        ):
            return ()
        observed_title = native_title.titles.read_title(self.context.source_reference)
        self._store_marker = native_title.title_store_marker(
            self.context.source_reference
        )
        self._checked_store = True
        if observed_title is None:
            return ()
        state_position = hashlib.sha256(
            f"{observed_title.origin}\0{observed_title.text}".encode("utf-8")
        ).hexdigest()
        if _title_state_position(after_position) == state_position:
            return ()
        position = _title_observation_position(
            state_position,
            self._store_marker,
        )
        observation = NativeTitleObservation(
            observed_title.text,
            observed_title.origin,
        )
        return (RawEvent(
            raw_event_id=RawEventId(f"{self.source_identity}:{position}"),
            harness=HARNESS,
            source_type=TITLE_SOURCE_TYPE,
            source_name=self.context.source_reference,
            source_position=position,
            session_id=self.context.session_id,
            actor_id=self.context.actor_id,
            parent_actor_id=self.context.parent_actor_id,
            observed_at=time.time(),
            encoding="json",
            payload=encode_document(observation),
            source_identity=self.source_identity,
        ),)


def _title_state_position(source_position: str | None) -> str | None:
    """Get the title state from a current or legacy source position."""
    if source_position is None:
        return None
    version, separator, remainder = source_position.partition(":")
    if version != "v2" or not separator:
        return source_position
    state_position, separator, _observation_position = remainder.partition(":")
    return state_position if separator else source_position


def _title_observation_position(
    state_position: str,
    store_marker: native_title.CodexTitleStoreMarker | None,
) -> str:
    """Make repeated title states distinct without replay after a restart.

    The state part lets a new source suppress an unchanged title. The store
    marker makes A -> B -> A a new observation instead of reusing the first A
    raw-event identity and losing the final change at the durable dedupe gate.
    """
    marker_value = (
        repr(
            (
                store_marker.database,
                store_marker.database_state,
                store_marker.write_ahead_state,
            )
        )
        if store_marker is not None
        else str(time.time_ns())
    )
    observation_position = hashlib.sha256(
        marker_value.encode("utf-8")
    ).hexdigest()
    return f"v2:{state_position}:{observation_position}"


@dataclass
class ChildRollouts:
    parent_session_id: CodexSessionId
    paths: tuple[str, ...]
    next_index: int = 0


@dataclass
class PendingRollout:
    path: str
    marker: tuple[int, int, int] | None = None


@dataclass(frozen=True)
class CodexSessionSources:
    session_id: SessionId
    source_reference: str
    owns_lead_session: bool
    sources: tuple[HarnessRawEventSource, ...]


class CodexRawEventSources(HarnessRawEventSources):
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._catalog = RolloutCatalog()
        self._clock = clock
        self._catalog_refreshed_at: float | None = None
        self._known_rollout_paths: frozenset[str] = frozenset()
        self._pending_rollouts: list[PendingRollout] = []
        self._child_parent_by_path: dict[str, CodexSessionId] = {}
        self._child_rollouts: list[ChildRollouts] = []
        self._sessions: dict[SessionId, CodexSessionSources] = {}
        self._child_sources: dict[
            tuple[SessionId, str, ActorRole], CodexRolloutRawEventSource
        ] = {}

    def release_session(self, session_id: SessionId) -> None:
        """Release rollout readers for one finished session."""
        self._sessions.pop(session_id, None)
        for key in tuple(self._child_sources):
            if key[0] == session_id:
                del self._child_sources[key]

    def _next_child_rollout(self, parent_codex_session_id: CodexSessionId) -> tuple[str, ...]:
        self._refresh_child_rollouts()
        selected_children = next(
            (child for child in self._child_rollouts if child.parent_session_id == parent_codex_session_id),
            None,
        )
        if selected_children is None or not selected_children.paths:
            return ()
        position = selected_children.next_index % len(selected_children.paths)
        selected_children.next_index += 1
        return (selected_children.paths[position],)

    def _refresh_child_rollouts(self) -> None:
        now = self._clock()
        if (
            self._catalog_refreshed_at is not None
            and now - self._catalog_refreshed_at < CATALOG_REFRESH_SECONDS
        ):
            return
        self._catalog_refreshed_at = now
        rollout_paths = frozenset(self._catalog.paths())
        removed = self._known_rollout_paths - rollout_paths
        added = rollout_paths - self._known_rollout_paths
        self._pending_rollouts = [
            pending
            for pending in self._pending_rollouts
            if pending.path not in removed
        ]
        for rollout_path in removed:
            self._child_parent_by_path.pop(rollout_path, None)
        for key in tuple(self._child_sources):
            if key[1] in removed:
                del self._child_sources[key]
        self._pending_rollouts.extend(PendingRollout(path) for path in added)
        changed = bool(removed or added)
        for pending in tuple(self._pending_rollouts):
            marker = self._file_marker(pending.path)
            if marker == pending.marker:
                continue
            pending.marker = marker
            metadata = session_metadata(pending.path)
            if metadata is None:
                continue
            self._pending_rollouts.remove(pending)
            parent_id = _parent_thread_id(metadata)
            if parent_id:
                self._child_parent_by_path[pending.path] = CodexSessionId(parent_id)
                changed = True
        self._known_rollout_paths = rollout_paths
        if not changed:
            return
        existing = {child.parent_session_id: child for child in self._child_rollouts}
        grouped: dict[CodexSessionId, list[str]] = {}
        for rollout_path, parent_id in self._child_parent_by_path.items():
            grouped.setdefault(parent_id, []).append(rollout_path)
        self._child_rollouts = [
            ChildRollouts(
                parent_id,
                tuple(sorted(paths)),
                existing.get(parent_id, ChildRollouts(parent_id, ())).next_index,
            )
            for parent_id, paths in sorted(grouped.items())
        ]

    @staticmethod
    def _file_marker(path: str) -> tuple[int, int, int] | None:
        try:
            status = os.stat(path)
        except OSError:
            return None
        return status.st_ino, status.st_mtime_ns, status.st_size

    def for_session(self, session: Session) -> tuple[HarnessRawEventSource, ...]:
        cached = self._sessions.get(session.session_id)
        if cached is None or cached.source_reference != session.source_reference:
            owns_lead_session = lead_rollout(session.source_reference)
            lead_sources: tuple[HarnessRawEventSource, ...] = (
                (
                    CodexRolloutRawEventSource(session.source_context),
                    CodexTitleRawEventSource(session.source_context),
                )
                if owns_lead_session
                else ()
            )
            cached = CodexSessionSources(
                session.session_id,
                session.source_reference,
                owns_lead_session,
                lead_sources,
            )
            self._sessions[session.session_id] = cached
        sources = list(cached.sources)
        for child_path in self._next_child_rollout(
            codex_session_id_from_domain(session.session_id)
        ):
            child_body_position = rollout.subagent_body_offset(child_path)
            if child_body_position == 0:
                continue
            actor_role = (
                ActorRole.CHILD if cached.owns_lead_session else ActorRole.SIDECAR
            )
            key = session.session_id, child_path, actor_role
            child_source = self._child_sources.get(key)
            if child_source is None:
                child_source = CodexRolloutRawEventSource(
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
                    actor_role,
                )
                self._child_sources[key] = child_source
            sources.append(child_source)
        return tuple(sources)
