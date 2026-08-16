"""The activity backlog: the block before this one, and the one before that."""

from __future__ import annotations

from dashboard.render.items import DashboardPresenter
from dashboard.services.models import DashboardActivityPage
from domain.ids import SessionId
from engine.projections import ActivityScope, SessionQueries
from engine.store.canonical import CanonicalEventStore


class DashboardActivityService:
    def __init__(
        self,
        canonical_store: CanonicalEventStore,
        queries: SessionQueries,
        presenter: DashboardPresenter | None = None,
    ) -> None:
        self.canonical_store = canonical_store
        self.queries = queries
        self.presenter = presenter or DashboardPresenter()

    def backlog(
        self,
        session_id: SessionId,
        before_cursor: int | None,
        scope: ActivityScope,
        block_count: int,
    ) -> DashboardActivityPage:
        snapshot_cursor = self.canonical_store.latest_cursor() or 0
        window = self.queries.activity_before(
            session_id,
            before_cursor,
            scope,
            block_count,
            through_cursor=snapshot_cursor,
        )
        return DashboardActivityPage(
            oldest_cursor=window.oldest_cursor,
            latest_cursor=snapshot_cursor,
            has_more=window.has_more,
            items=tuple(self.presenter.present(activity) for activity in window.activities),
        )
