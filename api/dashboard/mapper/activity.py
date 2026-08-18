"""Rendered feed items to the activity plane's models.

The rendered item and the response are separate declarations on purpose: the
presenters decide what an item SAYS (which is one canonical activity's whole
meaning, in escaped HTML), and this decides what the browser is told about it.
"""

from __future__ import annotations

from api.dashboard.mapper.sessions import canonical_snapshot
from api.dashboard.models.sessions.activity_frame import ActivityFrameResponse
from api.dashboard.models.sessions.activity_item import ActivityItemResponse
from api.dashboard.models.sessions.activity_page import ActivityPageResponse
from dashboard.render.items import DashboardItem
from dashboard.services.models import DashboardActivityFrame, DashboardActivityPage


def activity_item(item: DashboardItem) -> ActivityItemResponse:
    return ActivityItemResponse(
        item_id=item.item_id,
        item_type=item.item_type,
        summary_kind=item.summary_kind,
        actor_id=item.actor_id,
        state=item.state,
        html=item.html,
        plain_text=item.plain_text,
        content_reference=item.content_reference,
        conversation_kind=item.conversation_kind,
        turn_id=item.turn_id,
        final=item.final,
        actor_assignment_id=item.actor_assignment_id,
        actor_assignment_phase=item.actor_assignment_phase,
        lines_added=item.lines_added,
        lines_removed=item.lines_removed,
        message_id=item.message_id,
        reply_to_message_id=item.reply_to_message_id,
        started_at=item.started_at,
        finished_at=item.finished_at,
        command_reference=item.command_reference,
        output_reference=item.output_reference,
        file_path=item.file_path,
    )


def activity_page(page: DashboardActivityPage) -> ActivityPageResponse:
    return ActivityPageResponse(
        oldest_cursor=page.oldest_cursor,
        latest_cursor=page.latest_cursor,
        has_more=page.has_more,
        items=tuple(activity_item(item) for item in page.items),
    )


def activity_frame(frame: DashboardActivityFrame) -> ActivityFrameResponse:
    return ActivityFrameResponse(
        cursor=frame.cursor,
        items=tuple(activity_item(item) for item in frame.items),
        snapshot=canonical_snapshot(frame.snapshot),
    )
