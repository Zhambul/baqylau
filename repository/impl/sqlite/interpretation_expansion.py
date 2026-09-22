# Copyright (c) 2026 Zhambyl Yermagambet
"""Expand one normalized journal through its stored steps and bodies."""

import sqlite3

from pydantic import TypeAdapter

from domain.ids import RawEventId
from extensions.models import interpretation_normalization as normalization
from extensions.models.interpretation_bodies import BodyRef, BodyStore
from extensions.models.interpretation_normalization import NormalizedJournalHeader
from extensions.models.interpretation_storage_steps import StoredInterpretationStep
from extensions.models.interpretations import InterpretationProposal


def expand_normalized(
    connection: sqlite3.Connection, history_revision: str, raw_event_id: RawEventId, header_json: str,
) -> InterpretationProposal:
    """Restore one complete logical proposal from its stored references.

    Returns:
        The exact logical proposal that was stored.

    """
    header = NormalizedJournalHeader.model_validate_json(header_json)
    steps = _stored_steps(connection, history_revision, raw_event_id)
    store = _body_store(connection, history_revision, raw_event_id)
    return normalization.expand_journal(header, steps, store)


def _stored_steps(
    connection: sqlite3.Connection, history_revision: str, raw_event_id: RawEventId,
) -> tuple[StoredInterpretationStep, ...]:
    rows = connection.execute(
        "SELECT step FROM interpretation_journal_steps WHERE history_revision=? AND raw_event_id=? "
        "ORDER BY step_index", (history_revision, raw_event_id),
    ).fetchall()
    adapter: TypeAdapter[StoredInterpretationStep] = TypeAdapter(StoredInterpretationStep)
    return tuple(_stored_step(adapter, row) for row in rows)


def _stored_step(adapter: TypeAdapter[StoredInterpretationStep], row: sqlite3.Row) -> StoredInterpretationStep:
    return adapter.validate_json(str(row["step"]))


def _body_store(
    connection: sqlite3.Connection, history_revision: str, raw_event_id: RawEventId,
) -> BodyStore:
    rows = connection.execute(
        "SELECT body.digest, body.kind, body.byte_length, body.body FROM interpretation_bodies AS body "
        "JOIN interpretation_journal_bodies AS link ON link.digest=body.digest "
        "WHERE link.history_revision=? AND link.raw_event_id=?", (history_revision, raw_event_id),
    ).fetchall()
    store = BodyStore()
    for row in rows:
        encoded = str(row["body"]).encode("utf-8")
        store.adopt(_body_ref(row), encoded)
    return store


def _body_ref(row: sqlite3.Row) -> BodyRef:
    kind = row["kind"]
    digest = row["digest"]
    byte_length = int(row["byte_length"])
    return BodyRef(kind=kind, digest=digest, byte_length=byte_length)
