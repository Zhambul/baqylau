# Copyright (c) 2026 Zhambyl Yermagambet
"""Commit verdicts, journals, decoder state, and mixed facts as one operation."""

import sqlite3
from typing import NamedTuple

from baqylau_extension_api.models.canonical import CanonicalFact

from domain.ids import CanonicalEventId
from domain.records import CanonicalStorageResult
from extensions.models.interpretation_trace import validate_trace
from extensions.models.interpretations import InterpretationCommit, InterpretationOutcome, StoredCanonicalFact
from repository.impl.sqlite import (
    interpretation_acceptance as acceptance,
    interpretation_codec as codec,
    interpretation_facts as facts,
    interpretation_journal_writes as journal_writes,
    interpretation_reads as reads,
)


class FactAcceptance(NamedTuple):
    """Keep the stored first body with the result of this proposal."""

    stored: StoredCanonicalFact
    result: CanonicalStorageResult


def commit_interpretation(connection: sqlite3.Connection, request: InterpretationCommit) -> InterpretationOutcome:
    """Fence input and runtime, then commit the complete checked result.

    Returns:
        New acceptance, first-body convergence, or an exact prior commit.

    Raises:
        ValueError: If the snapshot is stale or the journal is inconsistent.

    """
    context = acceptance.select_context(connection, request)
    previous = reads.read_journal(connection, context.binding.history_revision, context.binding.raw_event_id)
    if previous is not None:
        if previous.proposal != request.proposal:
            message = "interpretation identity already has another complete proposal"
            raise ValueError(message)
        return _repeat(connection, request)
    acceptance.require_boundary(connection, context)
    acceptance.require_prior_state(connection, request)
    proposed = validate_trace(context, request.proposal)
    for fact in proposed:
        facts.validate_fact(context, fact)
    facts.validate_causes(connection, context.binding.history_revision, proposed)
    journal_writes.write_journal(connection, request)
    acceptance.write_decoder_state(connection, request)
    return _write_facts(connection, request)


def _write_facts(connection: sqlite3.Connection, request: InterpretationCommit) -> InterpretationOutcome:
    accepted: list[StoredCanonicalFact] = []
    deduplicated: list[StoredCanonicalFact] = []
    for position, fact in enumerate(request.proposal.facts):
        receipt = _accept_fact(connection, request, fact)
        if receipt.result == CanonicalStorageResult.ACCEPTED:
            accepted.append(receipt.stored)
        else:
            deduplicated.append(receipt.stored)
        connection.execute(
            "INSERT INTO interpretation_events(event_id, raw_event_id, event_order, storage_result, history_revision) "
            "VALUES(?, ?, ?, ?, ?)",
            (fact.event_id, request.proposal.binding.raw_event_id, position, receipt.result,
             request.proposal.binding.history_revision),
        )
    return InterpretationOutcome(accepted=tuple(accepted), deduplicated=tuple(deduplicated))


def _accept_fact(
    connection: sqlite3.Connection, request: InterpretationCommit, fact: CanonicalFact,
) -> FactAcceptance:
    history = request.proposal.binding.history_revision
    stored = reads.read_fact(connection, history, CanonicalEventId(fact.event_id))
    if stored is not None:
        facts.require_same_identity(stored.fact, fact)
        return FactAcceptance(stored, CanonicalStorageResult.DEDUPLICATED)
    codec.insert_fact(connection, fact, history, request.completed_at)
    stored = reads.read_fact(connection, history, CanonicalEventId(fact.event_id))
    if stored is None:
        message = "accepted canonical fact could not be read"
        raise RuntimeError(message)
    return FactAcceptance(stored, CanonicalStorageResult.ACCEPTED)


def _repeat(connection: sqlite3.Connection, request: InterpretationCommit) -> InterpretationOutcome:
    stored = tuple(
        reads.read_fact(connection, request.proposal.binding.history_revision, CanonicalEventId(fact.event_id))
        for fact in request.proposal.facts
    )
    if any(fact is None for fact in stored):
        message = "completed interpretation has a missing accepted fact"
        raise RuntimeError(message)
    deduplicated = tuple(fact for fact in stored if fact is not None)
    return InterpretationOutcome(repeated=True, deduplicated=deduplicated)
