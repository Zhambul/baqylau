"""Evidence, verdicts and facts: the four tables the interpreter turns.

Three protocols, one per aggregate:

    RawEventRepository            append-only observations, and the backlog
    TranslationEvidenceRepository the forensic join across all four tables
    CanonicalEventRepository      the interpretations, and every canonical read

`record_translation` is the only multi-table write in the system and it is ONE
method: verdict, facts and provenance in one transaction, decided inside the
repository. No caller ever holds a connection.
"""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence

from domain.ids import CanonicalEventId, RawEventId, SessionId
from domain.records import (
    CanonicalEventPage,
    StoredCanonicalEvent,
    TranslationEvidence,
    TranslationOutcome,
)
from harness.models import RawEvent, TranslationResult


class RawEventRepository(Protocol):
    """Owns `raw_events`. Append-only; nothing here interprets."""

    def record(self, raw_events: Sequence[RawEvent]) -> None:
        """Append observations. Re-recording an identical one is a no-op;
        reusing an id for DIFFERENT bytes raises `EventIdentityConflict` —
        that is corruption, not convergence."""
        ...

    def find(self, raw_event_id: RawEventId) -> RawEvent | None: ...

    def unverdicted(self, limit: int) -> tuple[RawEvent, ...]:
        """The backlog, in arrival order: evidence with no verdict yet.

        No registration filter: facts may precede their session — a session's
        first hook delivery translates into the `session.started` fact that
        births the row.
        """
        ...

    def latest_positions(self, source_identities: Sequence[str]) -> Mapping[str, str]:
        """Every named source's resume position, in one query.

        A pulled source resumes from the `source_position` of the last raw
        event carrying its identity, so recorded progress can never drift from
        the evidence. Bulk because the interpreter asks for every source it is
        about to read, on every tick.
        """
        ...


class TranslationEvidenceRepository(Protocol):
    """Read-only: one observation, its verdict, and the facts it produced."""

    def evidence(self, raw_event_id: RawEventId) -> TranslationEvidence | None: ...

    def evidence_for_session(self, session_id: SessionId) -> tuple[TranslationEvidence, ...]:
        """Every observation in one session, assembled in a fixed number of
        queries rather than four per event."""
        ...


class CanonicalEventRepository(Protocol):
    """Owns `canonical_events`, `translation_records` and `canonical_provenance`."""

    def record_translation(
        self,
        raw_event: RawEvent,
        translator_version: str,
        translation: TranslationResult,
        completed_at: float,
    ) -> TranslationOutcome:
        """Write the verdict, the events and their provenance in one transaction.

        A canonical event is an IDEMPOTENT projection: the identity names the
        fact, so re-observing it adds provenance and nothing else. The outcome
        separates what was newly accepted from what converged, so reactions run
        once per fact.
        """
        ...

    def find(self, event_id: CanonicalEventId) -> StoredCanonicalEvent | None: ...

    def session_ids(self) -> tuple[SessionId, ...]:
        """Every session that has a `session.started` fact, most recent first."""
        ...

    def latest_cursor(self) -> int | None: ...

    def latest_session_cursors(
        self,
        session_ids: Sequence[SessionId],
        through_cursor: int | None,
    ) -> Mapping[SessionId, int]:
        """The newest cursor per session, in one query. Absent when a session
        has no events at or below `through_cursor`."""
        ...

    def page_after(self, session_id: SessionId, cursor: int, limit: int) -> CanonicalEventPage: ...

    def page_through(self, session_id: SessionId, cursor: int | None) -> CanonicalEventPage: ...

    def page_tail(self, session_id: SessionId, cursor: int, limit: int) -> CanonicalEventPage: ...

    def events_of_types(
        self,
        session_id: SessionId,
        event_types: tuple[str, ...],
        through_cursor: int,
    ) -> tuple[StoredCanonicalEvent, ...]: ...

    def events_between(
        self,
        session_id: SessionId,
        after_cursor: int,
        through_cursor: int,
    ) -> tuple[StoredCanonicalEvent, ...]: ...
