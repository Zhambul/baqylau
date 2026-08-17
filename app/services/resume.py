"""Application query for sessions offered by the resume picker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.repository import RepositoryQueries
from harness.models import TerminalSessionState
from domain.ids import SessionId
from domain.values import AccountReference, ModelReference
from repository.contract.facts import CanonicalEventRepository
from engine.projections import SessionQueries


class TerminalSessionReader(Protocol):
    def state(self, session_id: SessionId) -> TerminalSessionState: ...


@dataclass(frozen=True)
class ResumableSession:
    session_id: SessionId
    title: str | None
    last_activity_at: float
    active: bool
    harness: str
    model: ModelReference | None
    effort: str | None
    account: AccountReference | None


class ResumableSessionService:
    def __init__(
        self,
        canonical_events: CanonicalEventRepository,
        sessions: SessionQueries,
        terminal: TerminalSessionReader,
        repositories: RepositoryQueries,
        result_limit: int,
    ) -> None:
        self.canonical_events = canonical_events
        self.sessions = sessions
        self.terminal = terminal
        self.repositories = repositories
        self.result_limit = result_limit

    def sessions_for(
        self,
        working_directory: str,
        search: str | None,
    ) -> tuple[ResumableSession, ...]:
        requested_directory = self.repositories.canonical_directory(working_directory)
        if not requested_directory:
            return ()
        search_text = (search or "").strip().lower()
        cursor = self.canonical_events.latest_cursor()
        rows = []
        for summary in self.sessions.sessions(cursor):
            if (
                self.repositories.canonical_directory(
                    summary.initial_working_directory
                )
                != requested_directory
            ):
                continue
            if search_text and search_text not in str(summary.session_id).lower() and (
                summary.title is None or search_text not in summary.title.lower()
            ):
                continue
            stored_events = self.canonical_events.page_through(summary.session_id, cursor).events
            last_activity_at = max(
                (
                    stored.event.occurred_at
                    if stored.event.occurred_at is not None
                    else stored.accepted_at
                )
                for stored in stored_events
            )
            rows.append(
                ResumableSession(
                    session_id=summary.session_id,
                    title=summary.title,
                    last_activity_at=last_activity_at,
                    active=self.terminal.state(summary.session_id).window_id is not None,
                    harness=summary.harness,
                    model=summary.model,
                    effort=summary.effort,
                    account=summary.account,
                )
            )
            if len(rows) == self.result_limit:
                break
        return tuple(rows)
