# Copyright (c) 2026 Zhambyl Yermagambet
"""Read Codex rollout and native-title observations."""

from __future__ import annotations

import hashlib
import os
import time

from domain import ids as domain_ids, messaging
from harness import contract as harness_contract, file_tail
from harness.impl.codex.canonical import source_catalog, title as native_title, title_paths
from harness.models import directives, raw_events
from repository.mapper import documents

HARNESS = domain_ids.HarnessName("codex")
EVENT_BATCH_SIZE = 100
ROLLOUT_OBSERVATION_VERSION = 4


class CodexRolloutRawEventSource(harness_contract.HarnessRawEventSource):
    """Read one rollout file as complete-line observations."""

    def __init__(
        self,
        raw_event_source_context: raw_events.RawEventSourceContext,
        child_body_position: int | None = None,
        actor_role: messaging.ActorRole | None = None,
    ) -> None:
        """Initialize the rollout source."""
        self.context = raw_event_source_context
        self.child_body_position = child_body_position
        self.actor_role = actor_role
        self.source_path = os.path.realpath(
            raw_event_source_context.source_reference,
        )
        self.tail = file_tail.CompleteLineTail(self.source_path)
        source_hash = hashlib.sha256(
            self.source_path.encode(source_catalog.TEXT_ENCODING),
        ).hexdigest()
        self.source_identity = f"codex:rollout:v{ROLLOUT_OBSERVATION_VERSION}:{source_hash}"

    def watch_paths(self) -> tuple[str, ...]:
        """List the rollout file.

        Returns:
            The rollout path.

        """
        return (self.source_path,)

    def read(self, after_position: str | None) -> tuple[raw_events.RawEvent, ...]:
        """Return the next complete rollout observations.

        Returns:
            The next complete rollout observations.

        """
        observations = [
            raw_events.RawEvent(
                raw_event_id=domain_ids.RawEventId(
                    f"{self.source_identity}:{line.position}",
                ),
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
            )
            for line in self.tail.read(after_position, EVENT_BATCH_SIZE)
        ]
        return tuple(observations)

    def _source_type(self, line_position: int) -> str:
        if self.child_body_position is not None and 0 < line_position < self.child_body_position:
            return f"{self.actor_role}_replay"
        return f"{self.actor_role}_rollout" if self.actor_role else "rollout"


class CodexTitleRawEventSource(harness_contract.HarnessRawEventSource):
    """Observe the native Codex index, which has no title event stream."""

    def __init__(
        self,
        raw_event_source_context: raw_events.RawEventSourceContext,
        title_repository: native_title.CodexThreadTitleRepository = native_title.titles,
    ) -> None:
        """Initialize the title source."""
        self.context = raw_event_source_context
        self.title_repository = title_repository
        self._checked_store = False
        self._store_marker: native_title.CodexTitleStoreMarker | None = None
        source_hash = hashlib.sha256(
            os.path.realpath(
                raw_event_source_context.source_reference,
            ).encode(source_catalog.TEXT_ENCODING),
        ).hexdigest()
        self.source_identity = f"codex:title:{source_hash}"

    def watch_paths(self) -> tuple[str, ...]:
        """List the native title database and its write log.

        Returns:
            The existing database path and its write-log path.

        """
        database = title_paths.state_database(
            self.context.source_reference, self.title_repository.configuration_directory,
        )
        return (database, f"{database}-wal") if database else ()

    def read(self, after_position: str | None) -> tuple[raw_events.RawEvent, ...]:
        """Return a native title observation when its state changes.

        Returns:
            A native title observation when its state changes.

        """
        store_marker = native_title.title_store_marker(
            self.context.source_reference,
            self.title_repository.configuration_directory,
        )
        if store_marker is not None and self._checked_store and store_marker == self._store_marker:
            return ()
        observed_title = self.title_repository.read_title(
            self.context.source_reference,
        )
        self._store_marker = native_title.title_store_marker(
            self.context.source_reference,
            self.title_repository.configuration_directory,
        )
        self._checked_store = True
        if observed_title is None:
            return ()
        state_position = hashlib.sha256(
            f"{observed_title.origin}\0{observed_title.text}".encode(),
        ).hexdigest()
        if _title_state_position(after_position) == state_position:
            return ()
        position = _title_observation_position(state_position, self._store_marker)
        observation = directives.NativeTitleObservation(
            observed_title.text,
            observed_title.origin,
        )
        return (
            raw_events.RawEvent(
                raw_event_id=domain_ids.RawEventId(
                    f"{self.source_identity}:{position}",
                ),
                harness=HARNESS,
                source_type=raw_events.TITLE_SOURCE_TYPE,
                source_name=self.context.source_reference,
                source_position=position,
                session_id=self.context.session_id,
                actor_id=self.context.actor_id,
                parent_actor_id=self.context.parent_actor_id,
                observed_at=time.time(),
                encoding="json",
                payload=documents.encode_document(observation),
                source_identity=self.source_identity,
            ),
        )


def _title_state_position(source_position: str | None) -> str | None:
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
    marker_value = (
        str(time.time_ns())
        if store_marker is None
        else repr(
            (
                store_marker.database,
                store_marker.database_state,
                store_marker.write_ahead_state,
            ),
        )
    )
    observation_position = hashlib.sha256(
        marker_value.encode(source_catalog.TEXT_ENCODING),
    ).hexdigest()
    return f"v2:{state_position}:{observation_position}"
