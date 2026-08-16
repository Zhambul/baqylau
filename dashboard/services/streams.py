"""The live feed: one cursor, one frame, everything that changed since."""

from __future__ import annotations

from dashboard.render.items import DashboardPresenter
from dashboard.services.models import DashboardActivityFrame
from dashboard.services.sessions import DashboardSessionService, TerminalSessionReader
from core.repository import RepositoryQueries
from domain.ids import SessionId
from engine.projections import ActivityScope, SessionQueries
from engine.store.canonical import CanonicalEventStore


class DashboardStreamService:
    def __init__(
        self,
        canonical_store: CanonicalEventStore,
        queries: SessionQueries,
        terminal: TerminalSessionReader,
        repositories: RepositoryQueries,
        presenter: DashboardPresenter | None = None,
    ) -> None:
        self.canonical_store = canonical_store
        self.queries = queries
        self.presenter = presenter or DashboardPresenter()
        self.sessions = DashboardSessionService(
            canonical_store, queries, terminal, repositories
        )

    def frame(
        self,
        session_id: SessionId,
        cursor: int,
        scope: ActivityScope,
        limit: int = 200,
    ) -> DashboardActivityFrame | None:
        examined = self.canonical_store.after(session_id, cursor, limit)
        if not examined.events:
            return None
        frame_cursor = examined.cursor
        activity_page = self.queries.activity_after(
            session_id,
            cursor,
            scope,
            limit,
            through_cursor=frame_cursor,
        )
        changed_event_ids = {
            str(stored.event.event_id)
            for stored in examined.events
        }
        items = tuple(
            self.presenter.present(activity)
            for activity in activity_page.activities
            if changed_event_ids.intersection(map(str, activity.context.source_event_ids))
        )
        return DashboardActivityFrame(
            cursor=frame_cursor,
            items=items,
            snapshot=self.sessions.snapshot_at(session_id, scope, frame_cursor),
        )
