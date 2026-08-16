"""One session's events, fetched once and kept warm.

Every projection is a fold over the same page, so the page — not the fold — is
where the caching belongs. Two shapes: the whole session up to a cursor (grown
incrementally as new facts land) and a bounded tail for the pane, which reads
only the last N events and must therefore carry the actors those events name.
"""

from __future__ import annotations

from threading import RLock

from domain.ids import SessionId
from engine.store.canonical import CanonicalEventPage, CanonicalEventStore, StoredCanonicalEvent


class EventPages:
    def __init__(self, canonical_store: CanonicalEventStore) -> None:
        self.canonical_store = canonical_store
        self._latest_pages: dict[SessionId, tuple[int | None, tuple[StoredCanonicalEvent, ...]]] = {}
        self._tail_pages: dict[tuple[SessionId, int], CanonicalEventPage] = {}
        self._latest_pages_lock = RLock()

    def tail(
        self,
        session_id: SessionId,
        event_limit: int,
        through_cursor: int,
    ) -> CanonicalEventPage:
        key = (session_id, event_limit)
        with self._latest_pages_lock:
            cached = self._tail_pages.get(key)
            if cached is not None and cached.cursor == through_cursor:
                return cached
        page = self.canonical_store.tail(session_id, through_cursor, event_limit)
        with self._latest_pages_lock:
            self._tail_pages[key] = page
        return page

    def through(
        self,
        session_id: SessionId,
        through_cursor: int | None = None,
    ) -> CanonicalEventPage:
        selected_cursor = (
            self.canonical_store.latest_cursor()
            if through_cursor is None
            else through_cursor
        )
        session_cursor = self.canonical_store.latest_session_cursor(session_id, selected_cursor)
        with self._latest_pages_lock:
            cached = self._latest_pages.get(session_id)
            if (
                cached is not None
                and cached[0] is not None
                and session_cursor is not None
                and cached[0] < session_cursor
            ):
                cached = (
                    session_cursor,
                    cached[1] + self.canonical_store.between(
                        session_id,
                        cached[0],
                        session_cursor,
                    ),
                )
                self._latest_pages[session_id] = cached
            elif cached is None or cached[0] != session_cursor:
                page = self.canonical_store.through(session_id, selected_cursor)
                cached = (session_cursor, page.events)
                self._latest_pages[session_id] = cached
            events = cached[1]
        page_cursor = events[-1].cursor if events else (selected_cursor or 0)
        return CanonicalEventPage(events, page_cursor, selected_cursor, False)

    def tail_with_actors(
        self,
        session_id: SessionId,
        event_limit: int,
        through_cursor: int,
    ) -> tuple[tuple[StoredCanonicalEvent, ...], bool]:
        """A tail page, plus the actor facts its events refer back to.

        The tail is a window, so the `actor.started` that named an actor is
        usually far behind it — without these the pane would render ids.
        """
        page = self.tail(session_id, event_limit, through_cursor)
        actor_events = self.canonical_store.events_of_types(
            session_id,
            ("actor.started", "actor.name_changed"),
            through_cursor,
        )
        recent_event_ids = {stored.event.event_id for stored in page.events}
        stored_events = tuple(
            sorted(
                (*(
                    stored for stored in actor_events
                    if stored.event.event_id not in recent_event_ids
                ), *page.events),
                key=lambda stored: stored.cursor,
            )
        )
        return stored_events, page.has_more
