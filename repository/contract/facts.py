"""Evidence, verdicts and facts: the four tables the interpreter turns.

Three protocols, one per aggregate:

    RawEventRepository            append-only observations, and the backlog
    RawEventAuditRepository the forensic join across all four tables
    CanonicalEventRepository      the interpretations, and every canonical read

`record_translation` is the only multi-table write in the system and it is ONE
method: interpretation, facts and interpretation events in one transaction, decided inside the
repository. No caller ever holds a connection.
"""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence

from domain.ids import CanonicalEventId, RawEventId, SessionId
from domain.records import (
    CommittedEvent,
    StoredCanonicalEvent,
    TranslationOutcome,
)
from harness.models import RawEvent, RawEventAudit, TranslationResult


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


class RawEventAuditRepository(Protocol):
    """Read-only: one observation, its verdict, and the facts it produced."""

    def audit(self, raw_event_id: RawEventId) -> RawEventAudit | None: ...

    def audits_for_session(self, session_id: SessionId) -> tuple[RawEventAudit, ...]:
        """Every observation in one session, assembled in a fixed number of
        queries rather than four per event."""
        ...


class CanonicalEventRepository(Protocol):
    """Owns `canonical_events`, `interpretations` and `interpretation_events`."""

    def record_translation(
        self,
        raw_event: RawEvent,
        translator_version: str,
        translation_result: TranslationResult,
        completed_at: float,
    ) -> TranslationOutcome:
        """Write the interpretation and its events in one transaction.

        A canonical event is an IDEMPOTENT projection: the identity names the
        fact, so re-observing it adds an interpretation event and nothing else. The outcome
        separates what was newly accepted from what converged, so reactions run
        once per fact.
        """
        ...

    def find(self, event_id: CanonicalEventId) -> StoredCanonicalEvent | None: ...

    def session_ids(self) -> tuple[SessionId, ...]:
        """Every session that has a `session.started` fact, most recent first."""
        ...

    def page_from(self, cursor: int, limit: int) -> tuple[CommittedEvent, ...]:
        """Every session's facts after `cursor`, in the order they were accepted.

        The reaction loop's whole input, and the one read that crosses sessions:
        reactions happen in commit order, not per session, because the order two
        sessions' facts arrived in is the order the world saw them.
        """
        ...

