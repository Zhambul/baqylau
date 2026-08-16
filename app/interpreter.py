"""The one interpreter: pull raw evidence, translate it, react to committed facts."""

from __future__ import annotations

import json
import threading
import time
from typing import Mapping

from harness.contract import CanonicalEventReaction, CoreTranslator, HarnessRawEventSource
from harness.models import LIVENESS_SOURCE_TYPE, RawEvent, Session, TranslationResult
from core.process import process_alive
from domain.ids import RawEventId
from engine.canonical_store import CanonicalEventStore, CanonicalEventStoreError
from harness.registry import HarnessRegistry
from engine.operation_output import OperationOutputStore
from engine.recorder import RawEventRecorder
from engine.sessions import SessionStore
from harness.services.controls import HarnessControlService

TICK_INTERVAL_SECONDS = 0.25
TRANSLATION_BATCH_SIZE = 500


def _audit_failure(where: str, context: dict) -> None:
    """Record a swallowed interpreter failure, then carry on.

    Imported lazily so this module keeps its import-time purity, and guarded so a
    broken auditor can never take down the interpreter it exists to explain.
    """
    try:
        from diagnostics import record

        record.error(str(context.get("session_id", "")), f"interpreter ({where})", context)
    except Exception:
        pass


class SessionLivenessSource(HarnessRawEventSource):
    """Built by the interpreter for every unfinished session. Emits ONE raw
    event when the CLI process is gone — the one finish signal every session
    has, wrapped or not.

    Position encoding: a latch — `exited` means the exit was already recorded.
    """

    def __init__(self, session: Session) -> None:
        if session.harness_process_id is None:
            # Never swallowed: the failure lands in the source-construction
            # audit every tick until the pid arrives.
            raise ValueError(f"session has no harness process id: {session.session_id}")
        self.session = session
        self.source_identity = (
            f"{session.plugin.info.name}:liveness:"
            f"{session.session_id}:{session.harness_process_id}"
        )

    def read(self, after_position: str | None) -> tuple[RawEvent, ...]:
        if after_position == "exited":
            return ()
        if process_alive(
            self.session.harness_process_id,
            self.session.plugin.info.cli_process_name,
        ):
            return ()
        return (RawEvent(
            raw_event_id=RawEventId(self.source_identity),
            harness=self.session.plugin.info.name,
            source_type=LIVENESS_SOURCE_TYPE,
            source_name=f"process:{self.session.harness_process_id}",
            source_position="exited",
            session_id=self.session.session_id,
            actor_id=self.session.lead_actor_id,
            parent_actor_id=None,
            observed_at=time.time(),
            encoding="json",
            payload=json.dumps(
                {"process_id": self.session.harness_process_id, "state": "exited"}
            ).encode("utf-8"),
            source_identity=self.source_identity,
        ),)


class Interpreter:
    """One process, one thread, one method: `tick()`.

    Everything outside this class only APPENDS evidence; everything here is the
    read-and-interpret side. The thread must outlive every failure it can
    observe — it is the ONE driver of pulling and translation, and nothing
    restarts it — so each step below is contained and audited, never fatal.
    """

    def __init__(
        self,
        sessions: SessionStore,
        harnesses: HarnessRegistry,
        recorder: RawEventRecorder,
        operation_output: OperationOutputStore,
        canonical_store: CanonicalEventStore,
        core_translators: Mapping[str, CoreTranslator],
        reactions: tuple[CanonicalEventReaction, ...],
        controls: HarnessControlService,
    ) -> None:
        self.sessions = sessions
        self.harnesses = harnesses
        self.recorder = recorder
        self.operation_output = operation_output
        self.canonical_store = canonical_store
        self.core_translators = core_translators
        self.reactions = reactions
        self.controls = controls  # handed to harness reactors per call

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception:
                _audit_failure("tick", {})
            stop_event.wait(TICK_INTERVAL_SECONDS)

    def tick(self) -> None:
        self._pull()
        self._translate()

    # --- pull: turn the outside world into recorded evidence -------------------

    def _pull(self) -> None:
        for session in self.sessions.watchable():
            try:
                sources = (
                    *session.plugin.sources.for_session(session),
                    *self.operation_output.for_session(session.session_id),
                    # ALWAYS built — no silent skip: a pid-less session raises,
                    # loudly, into the audit below.
                    SessionLivenessSource(session),
                )
            except Exception:
                _audit_failure("source construction", {"session_id": str(session.session_id)})
                continue
            for source in sources:
                self._pull_source(session, source)

    def _pull_source(self, session: Session, source: HarnessRawEventSource) -> None:
        # One unhappy source must never stop its siblings, nor the next session's.
        try:
            raw_events = source.read(self.recorder.position(source.source_identity))
            if raw_events:
                self.recorder.record(raw_events)
        except Exception:
            _audit_failure(
                "source read",
                {
                    "session_id": str(session.session_id),
                    "source_identity": getattr(source, "source_identity", ""),
                    "source": type(source).__name__,
                },
            )

    # --- translate: meaning decided, stored once, reacted to -------------------

    def _translate(self) -> None:
        for raw_event in self.canonical_store.unverdicted_raw_events(TRANSLATION_BATCH_SIZE):
            self._translate_one(raw_event)

    def _translate_one(self, raw_event: RawEvent) -> None:
        plugin = self.harnesses.plugin(raw_event.harness)
        translator = self.core_translators.get(raw_event.source_type, plugin.translator)
        try:
            translation = translator.translate(raw_event)
        except Exception as error:
            # Any translator problem is a decision, not a crash: the queue moves on.
            translation = TranslationResult(
                (), "translation_failed", f"{type(error).__name__}: {error}"
            )
        try:
            canonical_events = self.canonical_store.store_translation(
                raw_event, plugin.info.plugin_version, translation
            )
        except CanonicalEventStoreError as error:
            # A translator that produced inconsistent canonical events is a
            # verdict too — an unverdicted row would wedge the ordered backlog.
            self.canonical_store.store_translation(
                raw_event,
                plugin.info.plugin_version,
                TranslationResult((), "translation_failed", f"inconsistent canonical output: {error}"),
            )
            _audit_failure(
                "canonical consistency",
                {
                    "session_id": str(raw_event.session_id),
                    "raw_event_id": str(raw_event.raw_event_id),
                },
            )
            return
        for reaction in self.reactions:
            # Reaction-outer, events-inner: each reaction finishes the batch
            # before the next starts, so the sessions row is current before the
            # pane reaction anchors to it.
            for canonical_event in canonical_events:
                try:
                    reaction.react(canonical_event)
                except Exception:
                    _audit_failure(
                        type(reaction).__name__,
                        {
                            "session_id": str(canonical_event.session_id),
                            "event_id": str(canonical_event.event_id),
                        },
                    )
        for reactor in plugin.reactors:
            # Then the harness's own reactors — dispatched by the event's
            # harness, the control service handed over per call.
            for canonical_event in canonical_events:
                try:
                    reactor.react(canonical_event, self.controls)
                except Exception:
                    _audit_failure(
                        type(reactor).__name__,
                        {
                            "session_id": str(canonical_event.session_id),
                            "event_id": str(canonical_event.event_id),
                        },
                    )
