# Copyright (c) 2026 Zhambyl Yermagambet
"""Replay closed sessions into candidate histories, compare them, and switch them after the drain."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.models import canonical, documents

from core.work_queue import WorkKind
from engine.interpret.replay import ReplayInterpretation
from engine.react import fold
from repository.contract.history_reprocessing import (
    HistoryComparison,
    HistoryReprocessing,
    HistoryStores,
    ReprocessingRefusedError,
    ReprocessingState,
)

if TYPE_CHECKING:
    from domain.ids import SessionId
    from engine.worker import EngineWorker
    from extensions.models.interpretations import StoredCanonicalFact
    from extensions.processing_contract import ExtensionProcessingBatch
    from repository.contract.folded_session import FoldedSession

REPLAY_PAGE = 100
DEFAULT_HISTORY = "default"
CONTINUATION_SECONDS = 0.01
CONTINUATION_KEY = "history replay continuation"
REPLAY_FAILED_CODE = "host.replay_failed"
REPLAY_FAILED_MESSAGE = "A replayed input could not be interpreted, so the candidate history is incomplete."
UNFINISHED_CODE = "host.replay_unfinished"
UNFINISHED_MESSAGE = "The candidate history does not finish the session, so it cannot replace the live history."
SWITCH_REFUSED_CODE = "host.switch_refused"
LIFECYCLE_KINDS = ("session.started", "session.finished")


def reprocess(engine_worker: EngineWorker, sources: ExtensionProcessingBatch | None) -> None:
    """Advance candidate histories by one bounded pass, then run each requested switch.

    The switch runs after the reaction drain of the same pass, so the core
    consumer has read every live fact before the candidate replaces them.
    """
    histories = engine_worker.extension_services.histories
    if sources is None or histories is None:
        return
    replay = HistoryReplay(engine_worker, sources, histories)
    work = replay.build_pending()
    for candidate in histories.candidates.in_state(ReprocessingState.SWITCHING):
        replay.switch(candidate)
    engine_worker.work_queue.set_deadline(
        WorkKind.CANONICAL, CONTINUATION_SECONDS if work else None, CONTINUATION_KEY,
    )


class HistoryReplay:
    """Keep the engine worker, the retained batch, and the history store for one pass."""

    def __init__(
        self,
        engine_worker: EngineWorker,
        sources: ExtensionProcessingBatch,
        history_stores: HistoryStores,
    ) -> None:
        """Store the pass services."""
        self.engine_worker = engine_worker
        self.sources = sources
        self.histories = history_stores.candidates
        self.sessions = history_stores.sessions

    def build_pending(self) -> int:
        """Replay one page of each building candidate, or settle a complete one.

        Returns:
            The number of replayed inputs, so the caller can continue the work.

        """
        replayed = 0
        core = ReplayInterpretation(self.engine_worker.interpreter.translation)
        for candidate in self.histories.in_state(ReprocessingState.BUILDING):
            originals = self.sessions.session_observations(candidate.session_id, candidate.replay_cursor, REPLAY_PAGE)
            if not originals:
                self._settle(candidate)
                continue
            try:
                for original in originals:
                    self.sources.interpret_history(original, core, candidate.history_revision)
            except Exception:  # noqa: BLE001 -- One failed replay fails its candidate, never the live history.
                self._release(candidate.session_id)
                self.histories.settle(
                    candidate.history_revision, ReprocessingState.FAILED,
                    diagnostic=_diagnostic(REPLAY_FAILED_CODE, REPLAY_FAILED_MESSAGE),
                )
                continue
            self.histories.advance(candidate.history_revision, originals[-1].cursor)
            replayed += len(originals)
        return replayed

    def switch(self, history_reprocessing: HistoryReprocessing) -> None:
        """Fold the candidate and switch it; a refused switch returns the candidate to ready with its reason."""
        folded = self.fold(history_reprocessing.session_id, history_reprocessing.history_revision)
        try:
            self.histories.switch(history_reprocessing.history_revision, folded)
        except ReprocessingRefusedError as error:
            self.histories.settle(
                history_reprocessing.history_revision, ReprocessingState.READY,
                diagnostic=_diagnostic(SWITCH_REFUSED_CODE, str(error)),
            )

    def fold(self, session_id: SessionId, history_revision: str) -> FoldedSession:
        """Fold one session's core facts of one history with the live writers.

        Returns:
            The folded read model.

        """
        facts = self.sessions.session_facts(session_id, history_revision)
        return fold.fold_session(self.engine_worker.reaction_loop.dependencies, fold.core_events(facts))

    def _settle(self, history_reprocessing: HistoryReprocessing) -> None:
        self._release(history_reprocessing.session_id)
        session_id, history_revision = history_reprocessing.session_id, history_reprocessing.history_revision
        candidate_facts = self.sessions.session_facts(session_id, history_revision)
        comparison = self._compare(session_id, history_revision, len(candidate_facts))
        if _last_lifecycle(candidate_facts) != "session.finished":
            failed = _diagnostic(UNFINISHED_CODE, UNFINISHED_MESSAGE)
            self.histories.settle(history_revision, ReprocessingState.FAILED, comparison, failed)
            return
        self.histories.settle(history_revision, ReprocessingState.READY, comparison)

    def _compare(self, session_id: SessionId, history_revision: str, candidate_facts: int) -> HistoryComparison:
        live = self.fold(session_id, DEFAULT_HISTORY)
        candidate = self.fold(session_id, history_revision)
        return HistoryComparison(
            live_facts=len(self.sessions.session_facts(session_id, DEFAULT_HISTORY)),
            candidate_facts=candidate_facts,
            live_entries=len(live.entries),
            candidate_entries=len(candidate.entries),
            equal_entries=fold.equal_entries(live, candidate),
        )

    def _release(self, session_id: SessionId) -> None:
        services = self.engine_worker.interpreter.dependencies.services
        for plugin in services.harnesses.plugins():
            plugin.translator.release_session(session_id)


def _last_lifecycle(facts: tuple[StoredCanonicalFact, ...]) -> str | None:
    for stored in reversed(facts):
        kind = canonical.fact_type(stored.fact)
        if kind in LIFECYCLE_KINDS:
            return kind
    return None


def _diagnostic(code: str, message: str) -> str:
    return documents.Diagnostic(code=code, message=message).model_dump_json()
