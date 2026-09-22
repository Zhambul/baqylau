# Copyright (c) 2026 Zhambyl Yermagambet
"""Expose complete mixed interpretation writes and bounded history reads."""

from dataclasses import dataclass

from baqylau_extension_api.models.canonical import CoreFact, CoreStateSnapshot
from baqylau_extension_api.models.scopes import ExtensionScope
from baqylau_extension_api.models.translation_inputs import TranslationState
from pydantic import TypeAdapter

from core.work_queue import WorkKind
from domain.ids import CanonicalEventId, RawEventId
from extensions.models import interpretation_reads, interpretation_snapshot as snapshots
from extensions.models.interpretations import InterpretationCommit, InterpretationOutcome, StoredCanonicalFact
from repository.contract.interpretations import InterpretationRepository
from repository.impl.sqlite import (
    interpretation_reads as reads,
    interpretation_snapshot,
    interpretation_state,
    interpretation_writes,
)
from repository.impl.sqlite.connection import SqliteDatabase


@dataclass
class SqliteInterpretationRepository(InterpretationRepository):
    """Keep all writes inside the repository and all extension code outside the transaction."""

    database: SqliteDatabase

    def record_interpretation(self, request: InterpretationCommit) -> InterpretationOutcome:
        """Accept one validated complete proposal or preserve the original database.

        Returns:
            The accepted or converged stored bodies after commit.

        """
        checked = InterpretationCommit.model_validate(request)
        with self.database.write(*_work_notices(checked), notify_readers=False) as connection:
            return interpretation_writes.commit_interpretation(connection, checked)

    def find_interpretation(self, history_revision: str, raw_event_id: RawEventId) -> InterpretationCommit | None:
        """Read complete processing records independently of current activation.

        Returns:
            The original journal and completion time, or no journal.

        """
        reads.validate_page(history_revision, 0, 1)
        with self.database.read() as connection:
            return reads.read_journal(connection, history_revision, raw_event_id)

    def find_fact(self, history_revision: str, event_id: CanonicalEventId) -> StoredCanonicalFact | None:
        """Read one accepted core or extension fact from an exact history.

        Returns:
            The original stored body, or no matching identity.

        """
        reads.validate_page(history_revision, 0, 1)
        with self.database.read() as connection:
            return reads.read_fact(connection, history_revision, event_id)

    def facts_for_scope(
        self, history_revision: str, scope: ExtensionScope, after_cursor: int, limit: int,
    ) -> interpretation_reads.CanonicalPage:
        """Select the complete scope, including repository worktree identity.

        Returns:
            A bounded ordered page and the selected history head.

        """
        reads.validate_page(history_revision, after_cursor, limit)
        selected = TypeAdapter[ExtensionScope](ExtensionScope).validate_python(scope)
        with self.database.read() as connection:
            return reads.read_page(connection, history_revision, after_cursor, limit, selected.model_dump_json())

    def current_fact_page(self, after_cursor: int, limit: int) -> interpretation_reads.CanonicalPage:
        """Keep candidate histories outside the single ordered live dispatch stream.

        Returns:
            Both fact branches from the current default history.

        """
        reads.validate_page("default", after_cursor, limit)
        with self.database.read() as connection:
            return reads.read_page(connection, "default", after_cursor, limit)

    def translator_state(self, key: interpretation_reads.TranslationStateKey) -> TranslationState:
        """Capture complete state before calling the pure extension decoder.

        Returns:
            The current state, or explicit revision zero for an unused source.

        """
        selected = interpretation_reads.TranslationStateKey.model_validate(key)
        with self.database.read() as connection:
            return interpretation_state.read_state(connection, selected)

    def capture_prior_state(self, request: snapshots.PriorStateRequest) -> CoreStateSnapshot:
        """Capture exact scope state without loading a full page of large bodies.

        Returns:
            One bounded prefix and its actual completeness at the requested head.

        """
        selected = snapshots.PriorStateRequest.model_validate(request)
        with self.database.read() as connection:
            return interpretation_snapshot.capture_snapshot(connection, selected)


def _work_notices(request: InterpretationCommit) -> tuple[WorkKind, ...]:
    if request.proposal.binding.mode == "replay" or not request.proposal.facts:
        return ()
    core = (fact for fact in request.proposal.facts if isinstance(fact, CoreFact))
    if any(fact.payload.kind in {"session.started", "session.finished"} for fact in core):
        return WorkKind.CANONICAL, WorkKind.SOURCES
    return (WorkKind.CANONICAL,)
