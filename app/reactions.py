"""The core reaction pipeline: committed facts in, world out — one concern each."""

from __future__ import annotations

from dataclasses import replace

from contracts.harness import CanonicalEventReaction, Session
from contracts.terminal import SessionPaneRequest
from domain.events import (
    CanonicalEvent,
    OperationFinished,
    OperationOutputFinished,
    OperationOutputLocated,
    SessionFinished,
    SessionStarted,
)
from domain.ids import SessionId
from runtime.operation_output import OperationOutputStore
from runtime.recorder import RawEventRecorder
from runtime.sessions import SessionStore
from app import pane_preferences
from app.session_terminal import ApplicationTerminal


class SessionUpsertCanonicalEventReaction(CanonicalEventReaction):
    """The one writer of the sessions table.

    Birth: the session's own `session.started` fact carries the identity in its
    payload and the location in its envelope. Upkeep: any later fact whose
    envelope carries values refreshes the live columns — a resume in a new
    window updates the row with the first fact its first delivery commits.
    """

    def __init__(self, sessions: SessionStore) -> None:
        self.sessions = sessions

    def react(self, canonical_event: CanonicalEvent) -> None:
        payload = canonical_event.payload
        started = payload if isinstance(payload, SessionStarted) else None
        if started is None and canonical_event.terminal_window_id is None \
                and canonical_event.harness_process_id is None:
            return
        session = self.sessions.find_by_id(canonical_event.session_id)
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
        operation_output: OperationOutputStore,
        recorder: RawEventRecorder,
    ) -> None:
        self.operation_output = operation_output
        self.recorder = recorder

    def react(self, canonical_event: CanonicalEvent) -> None:
        payload = canonical_event.payload
        if isinstance(payload, OperationOutputLocated):
            self.operation_output.save(
                canonical_event.session_id,
                canonical_event.harness,
                canonical_event.actor_id,
                canonical_event.parent_actor_id,
                payload,
            )
        elif isinstance(payload, OperationFinished):
            # Ends foreground followings only (until='operation_finished');
            # affects zero rows for operations that never had an output file.
            self.operation_output.finish(canonical_event.session_id, str(payload.operation_id))
        elif isinstance(payload, OperationOutputFinished):
            # The background job's true end: stop following its file now
            # instead of stat-ing it for the rest of the session.
            self.operation_output.finish_output(
                canonical_event.session_id, str(payload.operation_id)
            )
        elif isinstance(payload, SessionFinished):
            self._drain_all(canonical_event.session_id)

    def _drain_all(self, session_id: SessionId) -> None:
        # A finished session leaves watchable(): read each remaining file to its
        # end and remove the row (and the tee file, when we created it).
        for source in self.operation_output.for_session(session_id):
            raw_events = source.read(self.recorder.position(source.source_identity))
            if raw_events:
                self.recorder.record(raw_events)
            self.operation_output.remove(
                session_id, source.operation_id, source.delete_source, source.source_path
            )


class PaneCanonicalEventReaction(CanonicalEventReaction):
    """The terminal display: open the session's panes at the window its own
    evidence recorded, close them when the session finishes."""

    def __init__(self, terminal: ApplicationTerminal, sessions: SessionStore) -> None:
        self.terminal = terminal
        self.sessions = sessions

    def react(self, canonical_event: CanonicalEvent) -> None:
        payload = canonical_event.payload
        if isinstance(payload, SessionFinished):
            self.terminal.close_session_panes(canonical_event.session_id)
        elif isinstance(payload, SessionStarted):
            self._open(canonical_event.session_id)

    def _open(self, session_id: SessionId) -> None:
        if self.terminal.session_panes_are_open(session_id):
            return
        # The session-upsert reaction already ran for this whole batch
        # (reaction-outer order), so the row exists and carries the window the
        # same delivery shipped.
        session = self.sessions.find_by_id(session_id)
        if session is None or session.terminal_window_id is None:
            return  # headless launch: no anchor, no panes
        self.terminal.open_session_panes(SessionPaneRequest(
            session_id,
            session.terminal_window_id,
            pane_preferences.width_percent(session.working_directory or ""),
        ))
