"""Shared test wiring for the record → register → interpret storage spine."""

from __future__ import annotations

import time

from contracts.harness import RawEvent, TranslationResult
from runtime.canonical_store import CanonicalEventStore
from runtime.harnesses import HarnessRegistry
from runtime.projections import SessionQueries
from runtime.recorder import RawEventRecorder
from runtime.sessions import SessionRegistry
from runtime.watches import WatchRegistry


class CanonicalRuntime:
    """One database, all four storage classes, and the record+translate shorthand."""

    def __init__(self, database_path: str, clock=time.time, harnesses: HarnessRegistry | None = None) -> None:
        self.database_path = str(database_path)
        self.store = CanonicalEventStore(self.database_path, clock=clock)
        self.recorder = RawEventRecorder(self.database_path)
        self.sessions = SessionRegistry(self.database_path, harnesses)
        self.watches = WatchRegistry(self.database_path)

    def register(self, harness: str, session) -> None:
        if self.sessions.find(session.session_id) is None:
            self.sessions.register(harness, session)

    def record(self, raw_event: RawEvent, translator_version: str, translation: TranslationResult):
        self.recorder.record((raw_event,))
        backlog = {raw.raw_event_id for raw in self.store.untranslated_raw_events(1_000_000)}
        if raw_event.raw_event_id not in backlog:
            return ()
        return self.store.store_translation(raw_event, translator_version, translation)

    def queries(self) -> SessionQueries:
        return SessionQueries(self.store, self.sessions)

    def __getattr__(self, name):
        return getattr(self.store, name)
