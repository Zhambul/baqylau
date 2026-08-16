"""A file the session touched: which one, what changed, how much."""

from __future__ import annotations

import html

from dashboard.render.items.item import (
    DashboardItem,
)
from engine.projections import FileActivity


def file_presentation(activity: FileActivity) -> tuple[str, str]:
    file = activity.file
    verb, color = {
        "read": ("Read", "rgb(97,175,239)"),
        "created": ("Write", "rgb(152,195,121)"),
        "updated": ("Update", "rgb(229,192,123)"),
        "deleted": ("Delete", "rgb(224,108,117)"),
        "renamed": ("Move", "rgb(229,192,123)"),
    }[file.action]
    if activity.outcome == "failed":
        color = "rgb(224,108,117)"
    escaped_path = html.escape(file.path)
    markup = (
        f'<span style="color:{color}">{verb}</span>'
        '<span style="color:rgb(92,99,112)">(</span>'
        f'<span style="color:rgb(171,178,191)">{escaped_path}</span>'
        '<span style="color:rgb(92,99,112)">)</span>'
    )
    text = f"{verb}({file.path})"
    counts = []
    if file.lines_added:
        counts.append((f"+{file.lines_added}", "rgb(152,195,121)"))
    if file.lines_removed:
        counts.append((f"-{file.lines_removed}", "rgb(224,108,117)"))
    if counts:
        markup += "  " + " ".join(
            f'<span style="color:{count_color}">{count}</span>'
            for count, count_color in counts
        )
        text += "  " + " ".join(count for count, _count_color in counts)
    return text, f'<pre class="opl">{markup}</pre>'


def present_file(activity: FileActivity) -> DashboardItem:
    actor_id = activity.context.actor_id
    text, markup = file_presentation(activity)
    return DashboardItem(
        activity.context.activity_id,
        "file",
        {
            "read": "file_read",
            "created": "file_write",
            "updated": "file_edit",
            "deleted": "file_edit",
            "renamed": "file_edit",
        }[activity.file.action],
        actor_id,
        (
            activity.outcome
            if activity.outcome in ("succeeded", "failed", "cancelled")
            else None
        ),
        markup,
        text,
        (
            f"{activity.content_event_id}:{activity.content_field}"
            if activity.content_event_id is not None and activity.content_field is not None
            else None
        ),
        lines_added=activity.file.lines_added,
        lines_removed=activity.file.lines_removed,
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
        file_path=activity.file.path,
    )
