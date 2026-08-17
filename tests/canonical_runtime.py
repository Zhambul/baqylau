"""Shared test wiring for the record → interpret storage spine."""

from __future__ import annotations

import time

from harness.models import RawEvent, TranslationResult
from harness.registry import HarnessRegistry
from engine.projections import SessionQueries
from repository.impl.sqlite.canonical_events import SqliteCanonicalEventRepository
from repository.impl.sqlite.databases import main_database
from repository.impl.sqlite.evidence import SqliteTranslationEvidenceRepository
from repository.impl.sqlite.operation_output import SqliteOperationOutputRepository
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from repository.impl.sqlite.sessions import SqliteSessionRepository
from repository.impl.sqlite.workspace import SqliteSessionWorkspaceRepository


class CanonicalRuntime:
    """One database, the repositories over it, and the record+translate shorthand."""

    def __init__(
        self,
        database_path: str,
        clock=time.time,
        harnesses: HarnessRegistry | None = None,
    ) -> None:
        self.database_path = str(database_path)
        self.database = main_database(self.database_path)
        self.clock = clock
        self.store = SqliteCanonicalEventRepository(self.database)
        self.recorder = SqliteRawEventRepository(self.database)
        self.sessions = SqliteSessionRepository(self.database, harnesses)
        self.operation_output = SqliteOperationOutputRepository(self.database)
        self.evidence = SqliteTranslationEvidenceRepository(self.database)
        self.workspaces = SqliteSessionWorkspaceRepository(self.database)

    def register(self, harness: str, session) -> None:
        self.sessions.save(harness, session)

    def record(self, raw_event: RawEvent, translator_version: str, translation: TranslationResult):
        self.recorder.record((raw_event,))
        backlog = {raw.raw_event_id for raw in self.recorder.unverdicted(1_000_000)}
        if raw_event.raw_event_id not in backlog:
            return ()
        outcome = self.store.record_translation(
            raw_event, translator_version, translation, self.clock()
        )
        return outcome.accepted

    def queries(self) -> SessionQueries:
        return SessionQueries(self.store, self.sessions)

    def __getattr__(self, name):
        return getattr(self.store, name)
