"""The one interpreter: pull raw events, translate them, react to committed facts."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Mapping

from domain.events import CanonicalEvent, EventPayload, SessionFinished, SessionStarted
from domain.records import RecordedTranslationDecision
from audit.failures import CoalescingFailureRecorder, FailureContext
from audit.recorder import AuditRecorder
from harness.contract import (
    CanonicalEventReaction,
    CoreTranslator,
    HarnessRawEventSource,
    SessionResumeRecorder,
    SessionTerminalState,
    TerminalWindows,
)
from harness.models import InterruptRegistry, RawEvent, Session, TranslationResult
from harness.registry import HarnessRegistry
from engine.interpret import output_source
from engine.interpret.interrupts import PendingInterruptSource
from engine.interpret.liveness import (
    ProcessProbe,
    SessionLivenessSource,
    SessionWindowLivenessSource,
)
from repository.contract.facts import CanonicalEventRepository, RawEventRepository
from repository.contract.shell_output import ShellOutputRepository
from repository.contract.sessions import SessionRepository

TICK_INTERVAL_SECONDS = 0.25
TERMINAL_SNAPSHOT_INTERVAL_SECONDS = 1.0
SHELL_OUTPUT_EXPIRY_INTERVAL_SECONDS = 60.0
TRANSLATION_BATCH_SIZE = 500


class TranslationConsistencyError(ValueError):
    """A translation does not agree with the raw event it came from."""


class TerminalSnapshotSampler:
    """Use one terminal window snapshot for several fast interpreter ticks."""

    def __init__(
        self,
        session_terminal_state: SessionTerminalState | None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._terminal = session_terminal_state
        self._clock = clock
        self._sampled_at: float | None = None
        self._windows: TerminalWindows = ()

    def sample(self) -> TerminalWindows:
        if self._terminal is None:
            return ()
        now = self._clock()
        if (
            self._sampled_at is None
            or now - self._sampled_at >= TERMINAL_SNAPSHOT_INTERVAL_SECONDS
        ):
            self._windows = self._terminal.windows()
            self._sampled_at = now
        return self._windows

    def invalidate(self) -> None:
        """Make the next sample read the terminal again.

        A launch can add a window between two interpreter ticks. The snapshot
        from before that launch must not be used to decide that the new window
        has already closed.
        """
        self._sampled_at = None


@dataclass(frozen=True)
class SessionSourceBatch:
    """All pull sources for one session in one interpreter cycle."""

    session: Session
    sources: tuple[HarnessRawEventSource, ...]


def checked(raw_event: RawEvent, translation_result: TranslationResult) -> TranslationResult:
    """Refuse a translation whose events disagree with their own raw event.

    These five rules compare a canonical event against the raw event it came
    from, which makes them a rule about TRANSLATING, not about storing — they
    used to live in the store because that was where the two objects met. Run
    here, before the transaction opens, a violation becomes an ordinary
    `translation_failed` verdict instead of a caught storage exception followed
    by a second write.
    """
    try:
        for event in translation_result.canonical_events:
            _check_consistency(raw_event, event)
    except TranslationConsistencyError as error:
        return TranslationResult(
            (), RecordedTranslationDecision.TRANSLATION_FAILED, f"inconsistent canonical output: {error}"
        )
    return translation_result


def _check_consistency(raw_event: RawEvent, event: CanonicalEvent[EventPayload]) -> None:
    if event.session_id != raw_event.session_id:
        raise TranslationConsistencyError("canonical event does not belong to its raw event session")
    if event.harness != raw_event.harness:
        raise TranslationConsistencyError("canonical event harness does not match its raw event")
    if event.actor_id != raw_event.actor_id:
        raise TranslationConsistencyError("canonical event actor does not match its raw event")
    if event.parent_actor_id != raw_event.parent_actor_id:
        raise TranslationConsistencyError("canonical event parent actor does not match its raw event")
    if event.parent_actor_id == event.actor_id:
        raise TranslationConsistencyError("an actor cannot be its own parent")


class Interpreter:
    """One process, one thread, one method: `tick()`.

    Everything outside this class only APPENDS raw events; everything here is the
    read-and-interpret side. The thread must outlive every failure it can
    observe — it is the ONE driver of pulling and translation, and nothing
    restarts it — so each step below is contained and audited, never fatal.

    It translates and it feeds itself, and nothing else. The two `inputs` are
    not reactions to a fact, they are TRANSLATION INPUTS: the pull phase reads
    the rows they write — the sessions row that says a session is watchable, and
    the follow list that says which output files to read — so they have to be
    current before the next pull, on this thread. Everything a fact CAUSES
    happens on the reaction loop, which follows the same facts through the
    canonical cursor (`engine/react/loop.py`).
    """

    def __init__(
        self,
        session_repository: SessionRepository,
        harness_registry: HarnessRegistry,
        raw_event_repository: RawEventRepository,
        shell_output_repository: ShellOutputRepository,
        canonical_event_repository: CanonicalEventRepository,
        core_translators: Mapping[str, CoreTranslator],
        inputs: tuple[CanonicalEventReaction, ...],
        audit_recorder: AuditRecorder,
        interrupt_registry: InterruptRegistry,
        clock: Callable[[], float] = time.time,
        session_terminal_state: SessionTerminalState | None = None,
        session_resume_recorder: SessionResumeRecorder | None = None,
    ) -> None:
        self.session_repository = session_repository
        self.harness_registry = harness_registry
        self.raw_event_repository = raw_event_repository
        self.shell_output_repository = shell_output_repository
        self.canonical_event_repository = canonical_event_repository
        self.core_translators = core_translators
        self.inputs = inputs
        self.audit_recorder = audit_recorder
        self.failures = CoalescingFailureRecorder(audit_recorder, "interpreter")
        self.interrupt_registry = interrupt_registry
        self.clock = clock
        self.terminal = session_terminal_state
        self.terminal_snapshots = TerminalSnapshotSampler(session_terminal_state)
        self.launch_effects = session_resume_recorder
        self._last_expiration_at: float | None = None
        # The liveness sources are rebuilt every tick; the probe's verified-pid
        # memory has to outlive them (engine/interpret/liveness.py ProcessProbe).
        self.liveness = ProcessProbe()

    def _audit_failure(
        self,
        where: str,
        failure_context: FailureContext,
    ) -> None:
        """Record a swallowed interpreter failure, then carry on.

        Guarded, so a broken auditor can never take down the interpreter it
        exists to explain.
        """
        self.failures.record(where, failure_context)

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception:
                self._audit_failure("tick", FailureContext())
            stop_event.wait(TICK_INTERVAL_SECONDS)

    def tick(self) -> None:
        terminal_windows = self.terminal_snapshots.sample()
        self._expire()
        self._discover_resumes(terminal_windows)
        self._pull(terminal_windows)
        self._translate()

    def _discover_resumes(self, terminal_windows: TerminalWindows) -> None:
        if self.terminal is None or self.launch_effects is None:
            return
        for plugin in self.harness_registry.plugins():
            locator = plugin.resume_locator
            if locator is None:
                continue
            for located_session in locator.locate(terminal_windows):
                session_id = located_session.session_id
                window_id = located_session.window_id
                session = self.session_repository.find(session_id)
                if session is None or session.terminal_window_id == window_id:
                    continue
                self.launch_effects.resumed(plugin.info.name, session_id, window_id)

    # --- expire: a following that outlived its ceiling -------------------------

    def _expire(self) -> None:
        # A two-hour safety ceiling does not need a write transaction four times
        # each second. The first cycle cleans old rows. Later cycles do this at
        # most once a minute.
        now = self.clock()
        if (
            self._last_expiration_at is not None
            and 0 <= now - self._last_expiration_at
            < SHELL_OUTPUT_EXPIRY_INTERVAL_SECONDS
        ):
            return
        try:
            output_source.expire(self.shell_output_repository, now)
        except Exception:
            self._audit_failure("output expiry", FailureContext())
        else:
            self._last_expiration_at = now

    # --- pull: turn the outside world into recorded raw events -----------------

    def _pull(self, terminal_windows: TerminalWindows) -> None:
        batches: list[SessionSourceBatch] = []
        for session in self.session_repository.watchable():
            try:
                if session.plugin is None:
                    # Same contract as the pid check inside
                    # SessionLivenessSource: a detached session cannot be
                    # watched, and saying so here sends it to the audit below
                    # named, instead of as an AttributeError from the next line.
                    raise ValueError(f"session has no attached harness plugin: {session.session_id}")
                sources = (
                    # Check the cheap exit latch first. If an old run has died,
                    # its run-scoped finish reaches the translation queue before
                    # a large source migration batch from that run.
                    self._liveness_source(session, terminal_windows),
                    *session.plugin.sources.for_session(session),
                    *output_source.sources_for_session(self.shell_output_repository, session.session_id),
                    PendingInterruptSource(session, self.interrupt_registry),
                )
            except Exception:
                self._audit_failure(
                    "source construction",
                    FailureContext(session_id=session.session_id),
                )
                continue
            batches.append(SessionSourceBatch(session, sources))
        identities = tuple(dict.fromkeys(
            source.source_identity
            for batch in batches
            for source in batch.sources
            if source.source_identity
        ))
        try:
            positions = self.raw_event_repository.latest_positions(identities)
        except Exception:
            for batch in batches:
                self._audit_failure(
                    "resume positions",
                    FailureContext(session_id=batch.session.session_id),
                )
            return
        for batch in batches:
            self._pull_sources(batch, positions)

    def _liveness_source(
        self,
        session: Session,
        terminal_windows: TerminalWindows,
    ) -> HarnessRawEventSource:
        if session.harness_process_id is not None:
            return SessionLivenessSource(session, self.liveness, terminal_windows)
        if self.terminal is not None:
            return SessionWindowLivenessSource(
                session,
                terminal_windows,
            )
        raise ValueError(f"session has no liveness source: {session.session_id}")

    def _pull_sources(
        self,
        session_source_batch: SessionSourceBatch,
        positions: Mapping[str, str],
    ) -> None:
        for source in session_source_batch.sources:
            self._pull_source(
                session_source_batch.session,
                source,
                positions.get(source.source_identity),
            )

    def _pull_source(
        self,
        session: Session,
        harness_raw_event_source: HarnessRawEventSource,
        after_position: str | None,
    ) -> None:
        # One unhappy source must never stop its siblings, nor the next session's.
        try:
            raw_events = harness_raw_event_source.read(after_position)
            if raw_events:
                self.raw_event_repository.record(raw_events)
        except Exception:
            self._audit_failure(
                "source read",
                FailureContext(
                    session_id=session.session_id,
                    source_identity=getattr(
                        harness_raw_event_source, "source_identity", ""
                    ),
                    source=type(harness_raw_event_source).__name__,
                ),
            )

    # --- translate: meaning decided, stored once, reacted to -------------------

    def _translate(self) -> None:
        for raw_event in self.raw_event_repository.unverdicted(TRANSLATION_BATCH_SIZE):
            self._translate_one(raw_event)

    def _translate_one(self, raw_event: RawEvent) -> None:
        plugin = self.harness_registry.plugin(raw_event.harness)
        translator = self.core_translators.get(raw_event.source_type, plugin.translator)
        try:
            translation = translator.translate(raw_event)
        except Exception as error:
            # Any translator problem is a decision, not a crash: the queue moves on.
            translation = TranslationResult(
                (), RecordedTranslationDecision.TRANSLATION_FAILED, f"{type(error).__name__}: {error}"
            )
        else:
            translation = checked(raw_event, translation)
        outcome = self.canonical_event_repository.record_translation(
            raw_event, plugin.info.plugin_version, translation, self.clock()
        )
        if any(
            isinstance(canonical_event.payload, SessionStarted)
            for canonical_event in outcome.accepted
        ):
            # A confirmed start can add a terminal window after the snapshot
            # sampled at the start of this tick. Force a current view before
            # liveness checks the new run on the next tick.
            self.terminal_snapshots.invalidate()
        for reaction in self.inputs:
            # Reaction-outer, events-inner: each input finishes the batch before
            # the next starts, so the sessions row is current before anything
            # anchors to it.
            for canonical_event in outcome.accepted:
                try:
                    reaction.react(canonical_event)
                except Exception:
                    self._audit_failure(
                        type(reaction).__name__,
                        FailureContext(
                            session_id=canonical_event.session_id,
                            event_id=canonical_event.event_id,
                        ),
                    )
        if any(
            isinstance(canonical_event.payload, SessionFinished)
            for canonical_event in outcome.accepted
        ):
            for name, release in (
                ("translation", plugin.translator.release_session),
                ("source", plugin.sources.release_session),
            ):
                try:
                    release(raw_event.session_id)
                except Exception:
                    self._audit_failure(
                        f"{name} memory release",
                        FailureContext(session_id=raw_event.session_id),
                    )
