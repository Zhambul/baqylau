"""Application query for sessions offered by the resume picker."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from core.repository import RepositoryQueries
from harness.models import TerminalSessionState
from domain.ids import SessionId
from domain.values import AccountReference, ModelReference
from repository.contract.session_data import SessionDataRepository


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
        session_data_repository: SessionDataRepository,
        terminal_session_reader: TerminalSessionReader,
        repository_queries: RepositoryQueries,
        result_limit: int,
    ) -> None:
        self.read_model = session_data_repository
        self.terminal = terminal_session_reader
        self.repositories = repository_queries
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
        rows = []
        # Newest first, so a `result_limit` cut keeps the sessions somebody is
        # most likely to want back.
        for data in sorted(
            self.read_model.visible(),
            key=lambda item: item.last_activity_at or item.session.started_at or 0.0,
            reverse=True,
        ):
            summary = data.session
            if (
                self.repositories.canonical_directory(summary.working_directory)
                != requested_directory
            ):
                continue
            if search_text and search_text not in str(summary.session_id).lower() and (
                summary.title is None or search_text not in summary.title.lower()
            ):
                continue
            lead = next(
                (actor for actor in data.actors if actor.actor_id == summary.lead_actor_id),
                None,
            )
            rows.append(
                ResumableSession(
                    session_id=summary.session_id,
                    title=summary.title,
                    last_activity_at=(
                        data.last_activity_at or summary.started_at or 0.0
                    ),
                    active=self.terminal.state(summary.session_id).window_id is not None,
                    harness=summary.harness,
                    model=lead.model if lead is not None else None,
                    effort=lead.effort if lead is not None else None,
                    account=summary.account,
                )
            )
            if len(rows) == self.result_limit:
                break
        return tuple(rows)
