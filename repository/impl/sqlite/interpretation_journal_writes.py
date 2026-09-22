# Copyright (c) 2026 Zhambyl Yermagambet
"""Write one normalized journal header, its bodies, and its ordered steps."""

import sqlite3

from extensions.models import interpretation_normalization as normalization
from extensions.models.interpretation_bodies import BodyStore, StoredBody
from extensions.models.interpretation_normalization import NormalizedJournal
from extensions.models.interpretations import MAX_INTERPRETATION_BYTES, InterpretationCommit

CODEC_VERSION = 2
_VERDICT_SQL = (
    "INSERT INTO interpretations(raw_event_id, translator_version, decision, reason, completed_at, "
    "history_revision, runtime_revision) VALUES(?, ?, ?, ?, ?, ?, ?)"
)
_HEADER_SQL = (
    "INSERT INTO interpretation_journals(history_revision, raw_event_id, proposal, codec_version) "
    "VALUES(?, ?, ?, ?)"
)
_BODY_SQL = (
    "INSERT OR IGNORE INTO interpretation_bodies(digest, kind, byte_length, body, created_at) "
    "VALUES(?, ?, ?, ?, ?)"
)
_BODY_LINK_SQL = (
    "INSERT INTO interpretation_journal_bodies(history_revision, raw_event_id, digest) VALUES(?, ?, ?)"
)
_STEP_SQL = (
    "INSERT INTO interpretation_journal_steps(history_revision, raw_event_id, step_index, stage, step) "
    "VALUES(?, ?, ?, ?, ?)"
)


def write_journal(connection: sqlite3.Connection, request: InterpretationCommit) -> None:
    """Write one complete normalized journal inside the current transaction.

    Raises:
        ValueError: If the normalized journal exceeds its size limit.

    """
    proposal = request.proposal
    binding = proposal.binding
    journal, store = normalization.normalize_proposal(proposal)
    if normalization.normalized_byte_length(journal, store) > MAX_INTERPRETATION_BYTES:
        message = "interpretation journal exceeds its normalized size limit"
        raise ValueError(message)
    _write_verdict(connection, request)
    _write_header(connection, request, journal)
    _write_bodies(connection, request, store)
    _write_steps(connection, request, journal)
    if binding.mode == "live":
        connection.execute("DELETE FROM pending_raw_events WHERE raw_event_id=?", (binding.raw_event_id,))


def _write_verdict(connection: sqlite3.Connection, request: InterpretationCommit) -> None:
    proposal = request.proposal
    binding = proposal.binding
    connection.execute(
        _VERDICT_SQL,
        (binding.raw_event_id, proposal.translator_version, proposal.decision, proposal.reason, request.completed_at,
         binding.history_revision, binding.runtime_revision),
    )


def _write_header(
    connection: sqlite3.Connection, request: InterpretationCommit, journal: NormalizedJournal,
) -> None:
    binding = request.proposal.binding
    connection.execute(
        _HEADER_SQL,
        (binding.history_revision, binding.raw_event_id, journal.header.model_dump_json(), CODEC_VERSION),
    )


def _write_bodies(connection: sqlite3.Connection, request: InterpretationCommit, store: BodyStore) -> None:
    binding = request.proposal.binding
    for body in store.bodies():
        _write_body(connection, request, body)
        connection.execute(
            _BODY_LINK_SQL, (binding.history_revision, binding.raw_event_id, body.ref.digest),
        )


def _write_body(connection: sqlite3.Connection, request: InterpretationCommit, body: StoredBody) -> None:
    ref = body.ref
    text = body.encoded.decode("utf-8")
    completed_at = request.completed_at
    connection.execute(_BODY_SQL, (ref.digest, ref.kind, ref.byte_length, text, completed_at))


def _write_steps(
    connection: sqlite3.Connection, request: InterpretationCommit, journal: NormalizedJournal,
) -> None:
    binding = request.proposal.binding
    for index, step in enumerate(journal.steps):
        connection.execute(
            _STEP_SQL,
            (binding.history_revision, binding.raw_event_id, index, step.stage, step.model_dump_json()),
        )
