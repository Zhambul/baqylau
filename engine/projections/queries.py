"""One door onto every projection of a session.

The folds are pure functions over a page of stored events; this is the object
that fetches the page and picks the fold. Keeping the two apart is what makes a
projection testable without a database — and what keeps this file a list of
one-liners instead of the thousand-line class it used to be.
"""

from __future__ import annotations

from domain.ids import ActorId, OperationId, SessionId
from engine.projections import attention as attention_fold
from engine.projections import paging
from engine.projections import session as session_fold
from engine.projections import tabstate
from engine.projections import usage as usage_fold
from engine.projections import work
from engine.projections.models import (
    ActivityPage,
    ActivityScope,
    ActivityStatistics,
    ActivityWindow,
    ActorSummary,
    AttentionState,
    BackgroundWorkSummary,
    ContextSummary,
    GoalState,
    OperationActivity,
    SessionSummary,
    TabState,
    TaskSummary,
    UsageSummary,
)
from engine.projections.pages import EventPages
from engine.store.canonical import CanonicalEventStore, StoredCanonicalEvent
from engine.store.sessions import SessionStore


class SessionQueries:
    def __init__(self, canonical_store: CanonicalEventStore, sessions: SessionStore) -> None:
        self.canonical_store = canonical_store
        self.session_registry = sessions
        self.pages = EventPages(canonical_store)

    def _events(
        self,
        session_id: SessionId,
        through_cursor: int | None,
    ) -> tuple[StoredCanonicalEvent, ...]:
        return self.pages.through(session_id, through_cursor).events

    # --- the session and its actors ------------------------------------------

    def sessions(self, through_cursor: int | None = None) -> tuple[SessionSummary, ...]:
        summaries = (
            self.summary(session_id, through_cursor)
            for session_id in self.canonical_store.session_ids()
        )
        return tuple(
            sorted(
                (summary for summary in summaries if summary is not None),
                key=lambda summary: summary.started_at,
                reverse=True,
            )
        )

    def summary(
        self,
        session_id: SessionId,
        through_cursor: int | None = None,
    ) -> SessionSummary | None:
        return session_fold.summary(session_id, self._events(session_id, through_cursor))

    def actors(
        self,
        session_id: SessionId,
        through_cursor: int | None = None,
    ) -> tuple[ActorSummary, ...]:
        return session_fold.actors(self._events(session_id, through_cursor))

    # --- the activity stream --------------------------------------------------

    def activity_after(
        self,
        session_id: SessionId,
        cursor: int,
        scope: ActivityScope,
        limit: int,
        through_cursor: int | None = None,
    ) -> ActivityPage:
        page = self.pages.through(session_id, through_cursor)
        return paging.after(page.events, page.latest_cursor, cursor, scope, limit)

    def activity_before(
        self,
        session_id: SessionId,
        before_cursor: int | None,
        scope: ActivityScope,
        block_count: int,
        through_cursor: int | None = None,
    ) -> ActivityWindow:
        events = self._events(session_id, through_cursor)
        return paging.before(events, before_cursor, scope, block_count)

    def activity_tail(
        self,
        session_id: SessionId,
        scope: ActivityScope,
        event_limit: int,
        activity_limit: int,
        through_cursor: int,
    ) -> ActivityWindow:
        events, has_more = self.pages.tail_with_actors(session_id, event_limit, through_cursor)
        return paging.tail(events, has_more, scope, activity_limit, through_cursor)

    # --- what it spent, what it is waiting on ---------------------------------

    def usage(self, session_id: SessionId, through_cursor: int | None = None) -> UsageSummary:
        return usage_fold.usage(self._events(session_id, through_cursor))

    def context(self, session_id: SessionId, through_cursor: int | None = None) -> ContextSummary:
        return usage_fold.context(self._events(session_id, through_cursor))

    def attention(self, session_id: SessionId, through_cursor: int | None = None) -> AttentionState:
        return attention_fold.attention(self._events(session_id, through_cursor))

    def tasks(
        self,
        session_id: SessionId,
        through_cursor: int | None = None,
    ) -> tuple[TaskSummary, ...]:
        return attention_fold.tasks(self._events(session_id, through_cursor))

    def goal(
        self,
        session_id: SessionId,
        through_cursor: int | None = None,
    ) -> GoalState | None:
        # The lead actor is the session's own voice: a subagent's goal is not
        # the session's, so the registry decides whose facts count here.
        session = self.session_registry.find_by_id(session_id)
        if session is None:
            return None
        events = self._events(session_id, through_cursor)
        return attention_fold.goal(events, session.lead_actor_id)

    # --- what it did ----------------------------------------------------------

    def background_work(
        self,
        session_id: SessionId,
        scope: ActivityScope,
        through_cursor: int | None = None,
    ) -> BackgroundWorkSummary:
        return work.background_work(self._events(session_id, through_cursor), scope)

    def background_operations(
        self,
        session_id: SessionId,
        scope: ActivityScope,
        through_cursor: int | None = None,
    ) -> tuple[OperationActivity, ...]:
        return work.background_operations(self._events(session_id, through_cursor), scope)

    def statistics(
        self,
        session_id: SessionId,
        scope: ActivityScope,
        through_cursor: int | None = None,
    ) -> ActivityStatistics:
        return work.statistics(self._events(session_id, through_cursor), scope)

    def active_seconds(
        self,
        session_id: SessionId,
        current_time: float,
        through_cursor: int | None = None,
    ) -> float:
        return work.active_seconds(self._events(session_id, through_cursor), current_time)

    def operation_activity(
        self,
        session_id: SessionId,
        actor_id: ActorId,
        operation_id: OperationId,
        through_cursor: int,
    ) -> OperationActivity:
        events = self._events(session_id, through_cursor)
        return work.operation_activity(events, actor_id, operation_id)

    # --- the one state a tab shows --------------------------------------------

    def tab_state(
        self,
        session_id: SessionId,
        through_cursor: int | None = None,
    ) -> TabState | None:
        return tabstate.tab_state(self._events(session_id, through_cursor), None)

    def tab_state_tail(
        self,
        session_id: SessionId,
        event_limit: int,
        through_cursor: int,
    ) -> TabState | None:
        # A window that opens mid-session never sees `session.started`, so it
        # starts from idle rather than from nothing.
        page = self.pages.tail(session_id, event_limit, through_cursor)
        return tabstate.tab_state(page.events, "idle")
