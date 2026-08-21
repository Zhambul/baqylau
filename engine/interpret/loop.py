"""The one interpreter: pull raw evidence, translate it, react to committed facts."""

from __future__ import annotations

import threading
import time
from typing import Callable, Mapping

from domain.events import CanonicalEvent, EventPayload
from audit.recorder import AuditRecorder
from harness.contract import (
    CanonicalEventReaction,
    CoreTranslator,
    HarnessRawEventSource,
)
from harness.models import InterruptRegistry, RawEvent, Session, TranslationResult
from harness.registry import HarnessRegistry
from engine.interpret import output_source
from engine.interpret.interrupts import PendingInterruptSource
from engine.interpret.liveness import ProcessProbe, SessionLivenessSource
from repository.contract.facts import CanonicalEventRepository, RawEventRepository
from repository.contract.shell_output import ShellOutputRepository
from repository.contract.sessions import SessionRepository

TICK_INTERVAL_SECONDS = 0.25
TRANSLATION_BATCH_SIZE = 500


class TranslationConsistencyError(ValueError):
    """A translation does not agree with the evidence it came from."""


def checked(raw_event: RawEvent, translation_result: TranslationResult) -> TranslationResult:
    """Refuse a translation whose events disagree with their own evidence.

    These five rules compare a canonical event against the raw event it came
    from, which makes them a rule about TRANSLATING, not about storing — they
    used to live in the store because that was where the two objects met. Run
    here, before the transaction opens, a violation becomes an ordinary
    `translation_failed` verdict instead of a caught storage exception followed
    by a second write.
    """
    try:
        for event in translation_result.canonical_events:
            _check_envelope(raw_event, event)
    except TranslationConsistencyError as error:
        return TranslationResult((), "translation_failed", f"inconsistent canonical output: {error}")
    return translation_result


def _check_envelope(raw_event: RawEvent, event: CanonicalEvent[EventPayload]) -> None:
    if event.session_id != raw_event.session_id:
        raise TranslationConsistencyError("canonical event does not belong to its raw event session")
    if event.harness != raw_event.harness:
        raise TranslationConsistencyError("canonical event harness does not match its raw evidence")
    if event.actor_id != raw_event.actor_id:
        raise TranslationConsistencyError("canonical event actor does not match its raw evidence")
    if event.parent_actor_id != raw_event.parent_actor_id:
        raise TranslationConsistencyError(
            "canonical event parent actor does not match its raw evidence"
        )
    if event.parent_actor_id == event.actor_id:
        raise TranslationConsistencyError("an actor cannot be its own parent")


class Interpreter:
    """One process, one thread, one method: `tick()`.

    Everything outside this class only APPENDS evidence; everything here is the
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
    ) -> None:
        self.session_repository = session_repository
        self.harness_registry = harness_registry
        self.raw_event_repository = raw_event_repository
        self.shell_output_repository = shell_output_repository
        self.canonical_event_repository = canonical_event_repository
        self.core_translators = core_translators
        self.inputs = inputs
        self.audit_recorder = audit_recorder
        self.interrupt_registry = interrupt_registry
        self.clock = clock
        # The liveness sources are rebuilt every tick; the probe's verified-pid
        # memory has to outlive them (engine/interpret/liveness.py ProcessProbe).
        self.liveness = ProcessProbe()

    def _audit_failure(self, where: str, context: dict[str, object]) -> None:
        """Record a swallowed interpreter failure, then carry on.

        Guarded, so a broken auditor can never take down the interpreter it
        exists to explain.
        """
        try:
            self.audit_recorder.error(
                str(context.get("session_id", "")), f"interpreter ({where})", context
            )
        except Exception:
            pass

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception:
                self._audit_failure("tick", {})
            stop_event.wait(TICK_INTERVAL_SECONDS)

    def tick(self) -> None:
        self._expire()
        self._pull()
        self._translate()

    # --- expire: a following that outlived its ceiling -------------------------

    def _expire(self) -> None:
        # An explicit step, once a tick. It used to happen inside the read that
        # listed the followings, so asking what was being followed could unlink
        # a file.
        try:
            output_source.expire(self.shell_output_repository, self.clock())
        except Exception:
            self._audit_failure("output expiry", {})

    # --- pull: turn the outside world into recorded evidence -------------------

    def _pull(self) -> None:
        for session in self.session_repository.watchable():
            try:
                if session.plugin is None:
                    # Same contract as the pid check inside
                    # SessionLivenessSource: a detached session cannot be
                    # watched, and saying so here sends it to the audit below
                    # named, instead of as an AttributeError from the next line.
                    raise ValueError(f"session has no attached harness plugin: {session.session_id}")
                sources = (
                    *session.plugin.sources.for_session(session),
                    *output_source.sources_for_session(self.shell_output_repository, session.session_id),
                    # ALWAYS built — no silent skip: a pid-less session raises,
                    # loudly, into the audit below.
                    SessionLivenessSource(session, self.liveness),
                    PendingInterruptSource(session, self.interrupt_registry),
                )
            except Exception:
                self._audit_failure("source construction", {"session_id": str(session.session_id)})
                continue
            self._pull_sources(session, sources)

    def _pull_sources(
        self,
        session: Session,
        sources: tuple[HarnessRawEventSource, ...],
    ) -> None:
        # One query for every source's resume position, not one each: a busy
        # machine has dozens of sources and this runs four times a second.
        identities = [getattr(source, "source_identity", "") for source in sources]
        try:
            positions = self.raw_event_repository.latest_positions([name for name in identities if name])
        except Exception:
            self._audit_failure("resume positions", {"session_id": str(session.session_id)})
            return
        for source in sources:
            self._pull_source(session, source, positions.get(source.source_identity))

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
                {
                    "session_id": str(session.session_id),
                    "source_identity": getattr(harness_raw_event_source, "source_identity", ""),
                    "source": type(harness_raw_event_source).__name__,
                },
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
                (), "translation_failed", f"{type(error).__name__}: {error}"
            )
        else:
            translation = checked(raw_event, translation)
        outcome = self.canonical_event_repository.record_translation(
            raw_event, plugin.info.plugin_version, translation, self.clock()
        )
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
                        {
                            "session_id": str(canonical_event.session_id),
                            "event_id": str(canonical_event.event_id),
                        },
                    )
