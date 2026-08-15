"""The one interpreter: pull raw evidence, translate it, react to committed facts."""

from __future__ import annotations

import json
import threading

from contracts.harness import (
    HarnessRawEventSource,
    RawEvent,
    Session,
    TERMINAL_SOURCE_TYPE,
    TranslationError,
    TranslationResult,
    WATCH_SOURCE_TYPE,
)
from contracts.terminal import SessionPaneRequest
from domain.events import SessionFinished, SessionStarted
from runtime.canonical_store import (
    CanonicalEventStore,
    CanonicalEventStoreError,
    StoredCanonicalEvent,
)
from runtime.harnesses import HarnessRegistry
from runtime.recorder import RawEventRecorder
from runtime.sessions import SessionRegistry
from runtime.watches import WatchRegistry
from app import pane_preferences
from app.services import HarnessControlService
from app.session_terminal import ApplicationTerminal

TICK_INTERVAL_SECONDS = 0.25
TRANSLATION_BATCH_SIZE = 500
REGISTRATION_BATCH_SIZE = 200


def _audit_failure(where: str, context: dict) -> None:
    """Record a swallowed interpreter failure, then carry on.

    Imported lazily so this module keeps its import-time purity, and guarded so a
    broken auditor can never take down the interpreter it exists to explain.
    """
    try:
        from core import audit

        audit.error(str(context.get("session_id", "")), f"interpreter ({where})", context)
    except Exception:
        pass


class Interpreter:
    """One process, one thread, one method: `tick()`.

    Everything outside this class only APPENDS evidence; everything here is the
    read-and-interpret side. The thread must outlive every failure it can
    observe — it is the ONE driver of pulling and translation, and nothing
    restarts it — so each step below is contained and audited, never fatal.
    """

    def __init__(
        self,
        sessions: SessionRegistry,
        harnesses: HarnessRegistry,
        recorder: RawEventRecorder,
        watches: WatchRegistry,
        canonical_store: CanonicalEventStore,
        controls: HarnessControlService,
        terminal: ApplicationTerminal,
    ) -> None:
        self.sessions = sessions
        self.harnesses = harnesses
        self.recorder = recorder
        self.watches = watches
        self.canonical_store = canonical_store
        self.controls = controls
        self.terminal = terminal

    def run(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                self.tick()
            except Exception:
                _audit_failure("tick", {})
            stop_event.wait(TICK_INTERVAL_SECONDS)

    def tick(self) -> None:
        self._register_from_evidence()
        self._pull()
        self._translate()

    # --- register: sessions the evidence itself announces -----------------------

    def _register_from_evidence(self) -> None:
        """The wrapper registers at launch; every other launch path lands here.

        Orphan evidence (raw events whose session has no row) is offered to its
        harness, which may derive the session from it — a hook payload carries
        the identity and the source reference. Evidence that names no session
        (a child actor's feed, a bare watch directive) simply stays orphaned
        and is retried with whatever arrives next.
        """
        undecided: set = set()
        for raw_event in self.canonical_store.unregistered_raw_events(REGISTRATION_BATCH_SIZE):
            if raw_event.session_id in undecided:
                continue
            try:
                plugin = self.harnesses.plugin(raw_event.harness)
                if plugin.session_evidence is None:
                    undecided.add(raw_event.session_id)
                    continue
                session = plugin.session_evidence.from_raw_event(raw_event)
                if session is None:
                    continue
                if session.session_id != raw_event.session_id:
                    raise ValueError(
                        f"session evidence names a different session: {session.session_id}"
                    )
                if self.sessions.find(session.session_id) is None:
                    self.sessions.register(raw_event.harness, session)
                undecided.add(raw_event.session_id)
            except Exception:
                undecided.add(raw_event.session_id)
                _audit_failure(
                    "session evidence",
                    {
                        "session_id": str(raw_event.session_id),
                        "raw_event_id": str(raw_event.raw_event_id),
                    },
                )

    # --- pull: turn the outside world into recorded evidence -------------------

    def _pull(self) -> None:
        for session in self.sessions.watchable():
            try:
                sources = (
                    *session.plugin.sources.for_session(session),
                    *self.watches.for_session(session.session_id),
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

    # --- translate: turn evidence into facts, exactly once ---------------------

    def _translate(self) -> None:
        for raw_event in self.canonical_store.untranslated_raw_events(TRANSLATION_BATCH_SIZE):
            try:
                self._translate_one(raw_event)
            except Exception:
                # The backlog is ordered, so a raw event that cannot even record
                # its own failure must not wedge everything behind it forever.
                _audit_failure(
                    "translation",
                    {
                        "session_id": str(raw_event.session_id),
                        "raw_event_id": str(raw_event.raw_event_id),
                    },
                )
                return

    def _translate_one(self, raw_event: RawEvent) -> None:
        plugin = self.harnesses.plugin(raw_event.harness)
        if raw_event.source_type == WATCH_SOURCE_TYPE:
            self.watches.apply(raw_event)
            self.canonical_store.store_translation(
                raw_event,
                plugin.info.plugin_version,
                TranslationResult((), "ignored_nonsemantic", "watch directive applied"),
            )
            return
        if raw_event.source_type == TERMINAL_SOURCE_TYPE:
            # A pane-anchor observation: read at react time, never translated.
            self.canonical_store.store_translation(
                raw_event,
                plugin.info.plugin_version,
                TranslationResult((), "ignored_nonsemantic", "terminal anchor recorded"),
            )
            return
        try:
            translation = plugin.translator.translate(raw_event)
        except TranslationError as error:
            self.canonical_store.store_translation_failure(
                raw_event, plugin.info.plugin_version, error
            )
            return
        except Exception as error:
            # A translator bug is still a verdict: the evidence is safe in
            # raw_events, and an unverdicted row would block the whole backlog.
            self.canonical_store.store_translation_failure(
                raw_event,
                plugin.info.plugin_version,
                TranslationError(f"translator raised {type(error).__name__}: {error}"),
            )
            _audit_failure(
                "translator bug",
                {
                    "session_id": str(raw_event.session_id),
                    "raw_event_id": str(raw_event.raw_event_id),
                },
            )
            return
        try:
            committed = self.canonical_store.store_translation(
                raw_event, plugin.info.plugin_version, translation
            )
        except CanonicalEventStoreError as error:
            # A translator that produced inconsistent canonical events is a
            # verdict too, for the same reason as above.
            self.canonical_store.store_translation_failure(
                raw_event,
                plugin.info.plugin_version,
                TranslationError(f"inconsistent canonical output: {error}"),
            )
            _audit_failure(
                "canonical consistency",
                {
                    "session_id": str(raw_event.session_id),
                    "raw_event_id": str(raw_event.raw_event_id),
                },
            )
            return
        for stored_event in committed:
            self._react(stored_event)
        if plugin.reactor is not None:
            try:
                plugin.reactor.react(raw_event, self.controls)
            except Exception:
                _audit_failure(
                    "reactor",
                    {
                        "session_id": str(raw_event.session_id),
                        "raw_event_id": str(raw_event.raw_event_id),
                    },
                )

    # --- react: the one place committed facts touch the world ------------------

    def _react(self, stored_event: StoredCanonicalEvent) -> None:
        payload = stored_event.event.payload
        if not isinstance(payload, (SessionStarted, SessionFinished)):
            return
        session_id = stored_event.event.session_id
        try:
            if isinstance(payload, SessionFinished):
                # A finished session leaves watchable(), so its watches would
                # never be pulled again: capture their tails and remove them now.
                # Background watches have no finish directive of their own — the
                # session's end IS their end.
                self._reap_watches(session_id)
                self.terminal.close_session_panes(session_id)
                return
            if self.terminal.session_panes_are_open(session_id):
                return
            # NEVER anchor by focus: this runs in the server, whose "current
            # window" is at best absent and at worst a stale identity inherited
            # from whichever hook spawned it. The anchor is either the session's
            # own tagged window (wrapper launches adopt their pending panes) or
            # the window a hook RECORDED from inside the session's tab.
            anchor_window_id = (
                self.terminal.window_for_session(session_id)
                or self._recorded_window(session_id)
            )
            if anchor_window_id is None:
                return
            session = self.sessions.load(session_id)
            self.terminal.open_session_panes(
                SessionPaneRequest(
                    session_id,
                    anchor_window_id,
                    pane_preferences.width_percent(session.working_directory or ""),
                )
            )
        except Exception:
            _audit_failure("session panes", {"session_id": str(session_id)})

    def _recorded_window(self, session_id) -> str | None:
        anchor = self.canonical_store.latest_raw_event(session_id, TERMINAL_SOURCE_TYPE)
        if anchor is None:
            return None
        try:
            window_id = str(json.loads(anchor.payload).get("window_id") or "")
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            return None
        return window_id or None

    def _reap_watches(self, session_id) -> None:
        for source in self.watches.for_session(session_id):
            raw_events = source.read(self.recorder.position(source.source_identity))
            if raw_events:
                self.recorder.record(raw_events)
            self.watches.remove(
                session_id, source.operation_id, source.delete_source, source.source_path
            )
