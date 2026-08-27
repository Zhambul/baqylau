"""The core reaction pipeline: committed facts in, world out — one concern each."""

from __future__ import annotations

import time
from dataclasses import replace

from core.repository import RepositoryQueries
from domain.events import (
    CanonicalEvent,
    EventPayload,
    SessionFinished,
    SessionStarted,
    ShellBackgrounded,
    ShellFinished,
    ShellOutputFinished,
    ShellOutputLocated,
    TurnAborted,
    TurnFinished,
)
from domain.ids import SessionId, ShellId
from domain.shells import ShellFollowState, ShellOutputFollowing
from engine.interpret import output_source
from harness.contract import CanonicalEventReaction
from harness.models import InterruptRegistry, Session
from repository.contract.facts import RawEventRepository
from repository.contract.shell_output import ShellOutputRepository
from repository.contract.sessions import SessionRepository


class SessionUpsertCanonicalEventReaction(CanonicalEventReaction):
    """The one writer of the sessions table.

    Birth: the session's own `session.started` fact carries the identity in its
    payload and the location in its stored event. Upkeep: any later fact whose
    stored event carries values refreshes the live columns — a resume in a new
    window updates the row with the first fact its first delivery commits.
    """

    def __init__(
        self,
        session_repository: SessionRepository,
        repository_queries: RepositoryQueries | None = None,
    ) -> None:
        self.sessions = session_repository
        self.repositories = repository_queries or RepositoryQueries()

    def react(self, canonical_event: CanonicalEvent[EventPayload]) -> None:
        payload = canonical_event.payload
        started = payload if isinstance(payload, SessionStarted) else None
        if (
            started is None
            and canonical_event.terminal_window_id is None
            and canonical_event.harness_process_id is None
        ):
            return
        session = self.sessions.find(canonical_event.session_id)
        if session is None:
            if started is None:
                return
            session = Session(
                session_id=canonical_event.session_id,
                lead_actor_id=canonical_event.actor_id,
                source_reference=started.source_reference,
                working_directory=started.working_directory or None,
                project_directory=(
                    self.repositories.project_directory(started.working_directory)
                    or None
                ),
            )
        project_directory = session.project_directory
        if project_directory is None:
            project_directory = (
                self.repositories.project_directory(
                    started.working_directory
                    if started is not None
                    else session.working_directory or ""
                )
                or None
            )
        self.sessions.save(
            canonical_event.harness,
            replace(
                session,
                terminal_window_id=(canonical_event.terminal_window_id or session.terminal_window_id),
                harness_process_id=(
                    canonical_event.harness_process_id
                    if started is not None
                    and (
                        canonical_event.terminal_window_id is not None
                        or canonical_event.harness_process_id is not None
                    )
                    else canonical_event.harness_process_id or session.harness_process_id
                ),
                project_directory=project_directory,
            ),
        )


class ShellOutputCanonicalEventReaction(CanonicalEventReaction):
    """The one-time moments in a command's life: its output file becomes
    known (start following), the command finishes (stop a foreground
    following), the harness announces a background job's true end (stop that
    following), the session finishes (drain everything). Output CHUNKS never
    pass through here — they are raw events, read by the collect phase."""

    def __init__(
        self,
        shell_output_repository: ShellOutputRepository,
        raw_event_repository: RawEventRepository,
    ) -> None:
        self.shell_output = shell_output_repository
        self.raw_events = raw_event_repository

    def react(self, canonical_event: CanonicalEvent[EventPayload]) -> None:
        payload = canonical_event.payload
        if isinstance(payload, ShellOutputLocated):
            self.shell_output.save(
                ShellOutputFollowing(
                    session_id=canonical_event.session_id,
                    shell_id=payload.shell_id,
                    harness=canonical_event.harness,
                    actor_id=canonical_event.actor_id,
                    parent_actor_id=canonical_event.parent_actor_id,
                    source_path=payload.source_path,
                    chunk_source_type=payload.chunk_source_type,
                    delete_source=payload.delete_source,
                    initial_size=payload.initial_size,
                    initial_modified_at=payload.initial_modified_at,
                    wait_for_source_change=payload.wait_for_source_change,
                    until=payload.until,
                    state=ShellFollowState.ACTIVE,
                    created_at=time.time(),
                )
            )
        elif isinstance(payload, ShellFinished):
            # Ends foreground followings only (until='shell_finished');
            # affects zero rows for commands that never had an output file.
            self.shell_output.mark_shell_finished(canonical_event.session_id, payload.shell_id)
        elif isinstance(payload, ShellBackgrounded):
            # Keep reading the file the job is still writing to. Without this the
            # `shell.finished` from the same raw event marks the row finishing,
            # one drain later the row is removed and — for a tee file we made —
            # the file is UNLINKED under a running process.
            self.shell_output.outlive_shell(canonical_event.session_id, payload.shell_id)
        elif isinstance(payload, ShellOutputFinished):
            # The background job's true end: stop following its file now
            # instead of stat-ing it for the rest of the session.
            self.shell_output.mark_finishing(canonical_event.session_id, payload.shell_id)
        elif isinstance(payload, SessionFinished):
            self._drain_all(canonical_event.session_id)

    def _drain_all(self, session_id: SessionId) -> None:
        # A finished session leaves watchable(): read each remaining file to its
        # end, remove the row, and unlink the tee file when we created it.
        followings = self.shell_output.find_for_session(session_id)
        positions = self.raw_events.latest_positions(
            [
                output_source.shell_output_source_identity(
                    following.harness,
                    following.session_id,
                    following.shell_id,
                    following.source_path,
                )
                for following in followings
            ]
        )
        for following in followings:
            source = output_source.ShellOutputRawEventSource(following, self.shell_output)
            raw_events = source.read(positions.get(source.source_identity))
            if raw_events:
                self.raw_events.record(raw_events)
            self.shell_output.remove(
                session_id,
                ShellId(str(following.shell_id)),
                following.source_path,
            )
            output_source.delete_source_file(following)


class InterruptCanonicalEventReaction(CanonicalEventReaction):
    """Clears `InterruptRegistry` the moment ANY turn-ending fact commits for a
    session — a genuine `Stop` hook that lands inside the grace period, or the
    registry's own fallback fact once it fires. Without this, a session that
    settles normally would still carry a stale mark, and a slower second
    interrupt could see it and think the first one is still pending."""

    def __init__(self, interrupt_registry: InterruptRegistry) -> None:
        self.interrupts = interrupt_registry

    def react(self, canonical_event: CanonicalEvent[EventPayload]) -> None:
        payload = canonical_event.payload
        if isinstance(payload, (TurnFinished, TurnAborted)):
            self.interrupts.clear(canonical_event.session_id)
