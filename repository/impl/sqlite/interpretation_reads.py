# Copyright (c) 2026 Zhambyl Yermagambet
"""Read accepted mixed bodies and complete journals from one explicit history."""

import sqlite3

from baqylau_extension_api.models.base import Identifier
from pydantic import StrictInt, TypeAdapter

from domain.ids import CanonicalEventId, RawEventId
from extensions.models.interpretation_reads import CanonicalPage
from extensions.models.interpretations import InterpretationCommit, InterpretationProposal, StoredCanonicalFact
from repository.impl.sqlite import (
    interpretation_codec,
    interpretation_expansion,
    interpretation_pages,
    interpretation_selection,
)

MAX_FACT_PAGE = 1000


def read_journal(
    connection: sqlite3.Connection, history_revision: str, raw_event_id: RawEventId,
) -> InterpretationCommit | None:
    """Retain the exact original proposal and the first completion time.

    Returns:
        A complete journal, or no journal for an unknown or legacy interpretation.

    """
    row = connection.execute(
        "SELECT journal.proposal, journal.codec_version, interpretation.completed_at "
        "FROM interpretation_journals AS journal "
        "JOIN interpretations AS interpretation USING(history_revision, raw_event_id) "
        "WHERE history_revision=? AND raw_event_id=?", (history_revision, raw_event_id),
    ).fetchone()
    if row is None:
        return None
    completed_at = float(row["completed_at"])
    if int(row["codec_version"]) == 1:
        return InterpretationCommit(
            proposal=InterpretationProposal.model_validate_json(row["proposal"]), completed_at=completed_at,
        )
    return InterpretationCommit(
        proposal=interpretation_expansion.expand_normalized(
            connection, history_revision, raw_event_id, str(row["proposal"]),
        ),
        completed_at=completed_at,
    )


def read_fact(
    connection: sqlite3.Connection, history_revision: str, event_id: CanonicalEventId,
) -> StoredCanonicalFact | None:
    """Keep accepted metadata separate from the transform candidate.

    Returns:
        The selected accepted body, or no fact for that history and ID.

    """
    row = connection.execute(
        "SELECT * FROM canonical_events WHERE history_revision=? AND event_id=?", (history_revision, event_id),
    ).fetchone()
    return None if row is None else interpretation_codec.stored_fact(row)


def read_page(
    connection: sqlite3.Connection, history_revision: str, after_cursor: int, limit: int, scope: str | None = None,
) -> CanonicalPage:
    """Read the complete history boundary and a bounded ordered page together.

    Returns:
        Accepted facts and the head from the same database snapshot.

    """
    rows = interpretation_pages.page_rows(connection, history_revision, after_cursor, limit, scope)
    return CanonicalPage(
        history_revision=history_revision, head=interpretation_selection.canonical_head(connection, history_revision),
        facts=tuple(interpretation_codec.stored_fact(row) for row in rows),
    )


def validate_page(history_revision: str, after_cursor: int, limit: int) -> None:
    """Reject ambiguous revisions, boolean limits, and unbounded reads.

    Raises:
        ValueError: If the page bounds are outside the supported range.

    """
    TypeAdapter[Identifier](Identifier).validate_python(history_revision)
    cursor = TypeAdapter[StrictInt](StrictInt).validate_python(after_cursor)
    count = TypeAdapter[StrictInt](StrictInt).validate_python(limit)
    if cursor < 0 or not 1 <= count <= MAX_FACT_PAGE:
        message = "canonical page cursor or limit is invalid"
        raise ValueError(message)
