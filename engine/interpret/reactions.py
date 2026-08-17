"""The core reaction pipeline: committed facts in, world out — one concern each."""

from __future__ import annotations

import time
from dataclasses import replace

from domain.events import (
    CanonicalEvent,
    OperationFinished,
    OperationOutputFinished,
    OperationOutputLocated,
    SessionFinished,
    SessionStarted,
    TurnAborted,
    TurnFinished,
)
from domain.ids import OperationId, SessionId
from domain.operations import OperationOutputFollowing
from engine.interpret import output_source
from harness.contract import CanonicalEventReaction
from harness.models import InterruptRegistry, Session
from repository.contract.facts import RawEventRepository
from repository.contract.operations import OperationOutputRepository
from repository.contract.sessions import SessionRepository


class SessionUpsertCanonicalEventReaction(CanonicalEventReaction):
    """The one writer of the sessions table.

    Birth: the session's own `session.started` fact carries the identity in its
    payload and the location in its envelope. Upkeep: any later fact whose
    envelope carries values refreshes the live columns — a resume in a new
    window updates the row with the first fact its first delivery commits.
    """

    def __init__(self, sessions: SessionRepository) -> None:
        self.sessions = sessions

    def react(self, canonical_event: CanonicalEvent) -> None:
        payload = canonical_event.payload
        started = payload if isinstance(payload, SessionStarted) else None
        if started is None and canonical_event.terminal_window_id is None \
                and canonical_event.harness_process_id is None:
            return
        session = self.sessions.find(canonical_event.session_id)
        if session is None:
            if started is None:
                return
            session = Session(
                session_id=canonical_event.session_id,
                lead_actor_id=canonical_event.actor_id,
                harness_session_id=str(canonical_event.session_id),
                source_reference=started.source_reference,
                working_directory=started.working_directory or None,
            )
        self.sessions.save(canonical_event.harness, replace(
            session,
            terminal_window_id=canonical_event.terminal_window_id or session.terminal_window_id,
            harness_process_id=canonical_event.harness_process_id or session.harness_process_id,
        ))


class OperationOutputCanonicalEventReaction(CanonicalEventReaction):
    """The one-time moments in an operation's life: its output file becomes
    known (start following), the operation finishes (stop a foreground
    following), the harness announces a background job's true end (stop that
    following), the session finishes (drain everything). Output CHUNKS never
    pass through here — they are evidence, read by the collect phase."""

    def __init__(
        self,
        operation_output: OperationOutputRepository,
        raw_events: RawEventRepository,
    ) -> None:
        self.operation_output = operation_output
        self.raw_events = raw_events

    def react(self, canonical_event: CanonicalEvent) -> None:
        payload = canonical_event.payload
        if isinstance(payload, OperationOutputLocated):
            self.operation_output.save(
                OperationOutputFollowing(
                    session_id=canonical_event.session_id,
                    operation_id=payload.operation_id,
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
                    state="active",
                    created_at=time.time(),
                )
            )
        elif isinstance(payload, OperationFinished):
            # Ends foreground followings only (until='operation_finished');
            # affects zero rows for operations that never had an output file.
            self.operation_output.mark_operation_finished(
                canonical_event.session_id, payload.operation_id
            )
        elif isinstance(payload, OperationOutputFinished):
            # The background job's true end: stop following its file now
            # instead of stat-ing it for the rest of the session.
            self.operation_output.mark_finishing(
                canonical_event.session_id, payload.operation_id
            )
        elif isinstance(payload, SessionFinished):
            self._drain_all(canonical_event.session_id)

    def _drain_all(self, session_id: SessionId) -> None:
        # A finished session leaves watchable(): read each remaining file to its
        # end, remove the row, and unlink the tee file when we created it.
        followings = self.operation_output.find_for_session(session_id)
        positions = self.raw_events.latest_positions([
            output_source.operation_output_source_identity(
                following.harness, following.session_id, str(following.operation_id)
            )
            for following in followings
        ])
        for following in followings:
            source = output_source.OperationOutputRawEventSource(
                following, self.operation_output
            )
            raw_events = source.read(positions.get(source.source_identity))
            if raw_events:
                self.raw_events.record(raw_events)
            self.operation_output.remove(
                session_id, OperationId(str(following.operation_id))
            )
            output_source.delete_source_file(following)


class InterruptCanonicalEventReaction(CanonicalEventReaction):
    """Clears `InterruptRegistry` the moment ANY turn-ending fact commits for a
    session — a genuine `Stop` hook that lands inside the grace period, or the
    registry's own fallback fact once it fires. Without this, a session that
    settles normally would still carry a stale mark, and a slower second
    interrupt could see it and think the first one is still pending."""

    def __init__(self, interrupts: InterruptRegistry) -> None:
        self.interrupts = interrupts

    def react(self, canonical_event: CanonicalEvent) -> None:
        payload = canonical_event.payload
        if isinstance(payload, (TurnFinished, TurnAborted)):
            self.interrupts.clear(canonical_event.session_id)
