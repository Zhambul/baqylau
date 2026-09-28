# Copyright (c) 2026 Zhambyl Yermagambet
"""Raw events, verdicts and facts: the four tables the interpreter turns.

Three protocols, one per aggregate:

    RawEventRepository            append-only observations, and the backlog
    RawEventAuditRepository the forensic join across all four tables
    CanonicalEventRepository      the interpretations, and every canonical read

`record_translation` writes a complete core interpretation: its verdict, facts,
and source links commit in one transaction inside the repository. Current reads
exclude unpublished histories and extension facts. No caller holds a connection.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from domain.event_base import CanonicalEvent, EventPayload
    from domain.ids import CanonicalEventId, RawEventId, SessionId
    from domain.records import TranslationOutcome
    from harness.models.raw_events import (
        RawEvent,
        RawEventAudit,
        TranslationResult,
    )


class RawEventRepository(Protocol):
    """Own the core harness branch of `raw_events` without interpreting it.

    ObservationRepository supplies extension writes and mixed ordered reads.
    This narrow contract keeps harness callers on their strict core models.
    """

    def record(self, raw_events: Sequence[RawEvent]) -> None:
        """Record.

        Append observations. Re-recording an identical one is a no-op;
                reusing an id for DIFFERENT bytes raises `EventIdentityConflictError` —
                that is corruption, not convergence.
        """
        ...

    def find(self, raw_event_id: RawEventId) -> RawEvent | None:
        """Return find."""
        ...

    def unverdicted(self, limit: int) -> tuple[RawEvent, ...]:
        """Return the unverdicted.

        The backlog, in arrival order: raw events with no verdict yet.

                No registration filter: facts may precede their session — a session's
                first hook delivery translates into the `session.started` fact that
                births the row.
        """
        ...

    def latest_positions(self, source_identities: Sequence[str]) -> Mapping[str, str]:
        """Every named source's resume position, in one query.

        A pulled source resumes from the `source_position` of the last raw
        event carrying its identity, so recorded progress can never drift from
        the raw events. Bulk because the interpreter asks for every source it is
        about to read, on every tick.
        """
        ...


class RawEventAuditRepository(Protocol):
    """Read-only: one observation, its verdict, and the facts it produced."""

    def audit(self, raw_event_id: RawEventId) -> RawEventAudit | None:
        """Return the audit."""
        ...

    def audits_for_session(self, session_id: SessionId) -> tuple[RawEventAudit, ...]:
        """Return the audits for session.

        Every observation in one session, assembled in a fixed number of
                queries rather than four per event.
        """
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

    def find(self, event_id: CanonicalEventId) -> CanonicalEvent[EventPayload] | None:
        """Return the find.

        The fact, its `raw_event_ids` filled in — the one read that pays for
                the audit join, because it is the one caller that looks at them.
        """
        ...

    def session_ids(self) -> tuple[SessionId, ...]:
        """Every session that has a `session.started` fact, most recent first."""
        ...
