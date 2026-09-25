# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep a finished live session, its read model, and the candidate history store together."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from domain.ids import SessionId
from engine.history_stage import HistoryReplay
from engine.react.loop import ReactionLoop
from repository.contract.history_reprocessing import HistoryReprocessing, HistoryStores, ReprocessingState
from repository.impl.sqlite import connection, history_reprocessing, read_model_views, session_data, session_history
from tests.extension_host import history_live_fixture as live, lifecycle_pipeline_fixture as pipelines

if TYPE_CHECKING:
    from pathlib import Path

    from engine.worker import EngineWorker


@dataclass(frozen=True)
class HistoryCase:
    """Keep the live pipeline, the read model, and the candidate store together."""

    pipeline: pipelines.LifecycleCase
    database: connection.SqliteDatabase
    loop: ReactionLoop
    histories: history_reprocessing.SqliteHistoryReprocessingRepository

    @property
    def session_id(self) -> SessionId:
        """The fixture session."""
        with self.database.read() as reader:
            row = reader.execute("SELECT session_id FROM sessions").fetchone()
        return SessionId(row["session_id"])

    def replay(self) -> HistoryReplay:
        """Build the history stage over the real batch, translation phase, and writers.

        Returns:
            The stage for one pass.

        """
        plugin = self.pipeline.core.plugin
        harnesses = SimpleNamespace(plugins=lambda: (plugin,))
        worker = SimpleNamespace(
            interpreter=SimpleNamespace(
                translation=self.pipeline.core.phase,
                dependencies=SimpleNamespace(services=SimpleNamespace(harnesses=harnesses)),
            ),
            reaction_loop=self.loop,
        )
        stores = HistoryStores(
            candidates=self.histories, sessions=session_history.SqliteSessionHistoryReader(self.database),
        )
        return HistoryReplay(cast("EngineWorker", worker), self.pipeline.pipeline.batch(), stores)

    def entries(self) -> list[tuple[str, str]]:
        """Read the session's feed identities and types.

        Returns:
            The feed rows in order.

        """
        store = session_data.SqliteSessionDataRepository(self.database)
        page = store.entries_page(self.session_id, limit=100).entries
        return [(str(entry.entry_id), entry.entry_type) for entry in page]

    def facts(self, history_revision: str = "default") -> list[tuple[str, int]]:
        """Read the facts of the session in one history.

        Returns:
            Event identities and cursors.

        """
        reader = session_history.SqliteSessionHistoryReader(self.database)
        facts = reader.session_facts(self.session_id, history_revision)
        return [(stored.fact.event_id, stored.cursor) for stored in facts]

    def stored(self, history_revision: str) -> HistoryReprocessing:
        """Read one stored candidate that must exist.

        Returns:
            The candidate.

        """
        candidate = self.histories.read(history_revision)
        assert candidate is not None
        return candidate

    def archive(self) -> HistoryReprocessing:
        """Read the one retired history.

        Returns:
            The retired history.

        """
        retired = self.histories.in_state(ReprocessingState.RETIRED)
        assert len(retired) == 1
        return retired[0]

    def view_revision(self) -> int:
        """Read the revision that stream clients compare to find a reset.

        Returns:
            The view revision.

        """
        with self.database.read() as reader:
            return read_model_views.revision(reader)


def a_case(tmp_path: Path) -> HistoryCase:
    """Interpret and fold one finished Claude session live.

    Returns:
        The case with a finished session and its read model.

    """
    pipeline, loop = live.finished_session(tmp_path)
    database = pipeline.pipeline.original.store.database
    return HistoryCase(pipeline, database, loop, history_reprocessing.SqliteHistoryReprocessingRepository(database))


def build(case: HistoryCase, history_revision: str) -> HistoryReprocessing:
    """Run the history stage until the candidate is no longer building.

    Returns:
        The settled candidate.

    """
    replay = case.replay()
    replayed = replay.build_pending()
    while replayed:
        replayed = replay.build_pending()
    stored = case.stored(history_revision)
    assert stored.state != ReprocessingState.BUILDING
    return stored


@dataclass(frozen=True)
class Switched:
    """Keep the state before a switch and the case after it."""

    case: HistoryCase
    candidate: HistoryReprocessing
    live: list[tuple[str, int]]
    feed: list[tuple[str, str]]
    view_revision: int


def switched(tmp_path: Path) -> Switched:
    """Build a candidate, request its switch, and run the switch.

    Returns:
        The case after the switch, with the state before it.

    """
    case = a_case(tmp_path)
    live_facts, feed = case.facts(), case.entries()
    candidate = case.histories.create(case.session_id)
    build(case, candidate.history_revision)
    case.histories.settle(candidate.history_revision, ReprocessingState.SWITCHING)
    view_revision = case.view_revision()
    case.replay().switch(candidate)
    return Switched(case, candidate, live_facts, feed, view_revision)
