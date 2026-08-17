"""Three ways to ask for a slice of the activity stream.

    after   what CHANGED since a cursor — a live client's forward poll
    before  the block before a position — scrolling back through history
    tail    the last N, for a pane that opens onto a session already running

Forward paging orders by revision, backward paging by position: the same
activity may move in one ordering and not the other.
"""

from __future__ import annotations

from engine.projections.activity import activities_of
from engine.projections.models import (
    ActivityPage,
    ActivityScope,
    ActivityWindow,
    OperationActivity,
)
from domain.records import StoredCanonicalEvent


def after(
    stored_events: tuple[StoredCanonicalEvent, ...],
    latest_cursor: int | None,
    cursor: int,
    scope: ActivityScope,
    limit: int,
) -> ActivityPage:
    activities, _position_cursors, revision_cursors = activities_of(stored_events, scope)
    changed = sorted(
        (
            activity
            for activity in activities
            if revision_cursors[activity.context.activity_id] > cursor
        ),
        key=lambda activity: revision_cursors[activity.context.activity_id],
    )
    selected = tuple(changed[:limit])
    page_cursor = (
        max(revision_cursors[activity.context.activity_id] for activity in selected)
        if changed
        else latest_cursor or cursor
    )
    return ActivityPage(page_cursor, latest_cursor, selected)


def before(
    stored_events: tuple[StoredCanonicalEvent, ...],
    before_cursor: int | None,
    scope: ActivityScope,
    block_count: int,
) -> ActivityWindow:
    activities, position_cursors, _revision_cursors = activities_of(stored_events, scope)
    eligible = [
        activity
        for activity in activities
        if before_cursor is None or position_cursors[activity.context.activity_id] < before_cursor
    ]
    selected = tuple(eligible[-block_count:])
    cursors = [position_cursors[activity.context.activity_id] for activity in selected]
    return ActivityWindow(
        oldest_cursor=min(cursors, default=before_cursor or 0),
        activities=selected,
        has_more=len(eligible) > len(selected),
    )


def tail(
    stored_events: tuple[StoredCanonicalEvent, ...],
    page_has_more: bool,
    scope: ActivityScope,
    activity_limit: int,
    through_cursor: int,
) -> ActivityWindow:
    activities, position_cursors, _revision_cursors = activities_of(stored_events, scope)
    complete = [
        activity for activity in activities
        if not isinstance(activity, OperationActivity) or activity.native_name is not None
    ]
    selected = tuple(complete[-activity_limit:])
    cursors = [position_cursors[activity.context.activity_id] for activity in selected]
    return ActivityWindow(
        oldest_cursor=min(cursors, default=through_cursor),
        activities=selected,
        has_more=page_has_more or len(complete) > len(selected),
    )
