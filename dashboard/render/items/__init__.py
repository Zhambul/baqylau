"""Canonical activity in, one rendered item out — a module per kind of thing.

    item.py        the shape itself, and the pieces every kind shares
    messages.py    what was said: prompts, replies, reasoning, actor messages
    operations.py  what it ran: commands, searches, tool calls
    files.py       what it touched
    attention.py   what it asked you, and what you answered
    work.py        how it organised itself: tasks, compaction, assignments

The dispatch below is a registry, not an if/elif ladder (docs/styleguide.md): a
new activity kind adds a module and one row, and nothing here changes shape.
An activity with no row is a bug, not a default — it raises.
"""

from __future__ import annotations

from dashboard.render.items import attention, files, messages, operations, work
from dashboard.render.items.item import DashboardItem
from engine.projections import (
    Activity,
    ActorAssignmentActivity,
    ActorMessageActivity,
    AttentionActivity,
    CompactionActivity,
    FileActivity,
    MessageActivity,
    OperationActivity,
    ReasoningActivity,
    TaskActivity,
)

_PRESENTERS = {
    MessageActivity: messages.present_message,
    ReasoningActivity: messages.present_reasoning,
    ActorMessageActivity: messages.present_actor_message,
    OperationActivity: operations.present_operation,
    FileActivity: files.present_file,
    AttentionActivity: attention.present_attention,
    TaskActivity: work.present_task,
    CompactionActivity: work.present_compaction,
    ActorAssignmentActivity: work.present_assignment,
}


class DashboardPresenter:
    def present(self, activity: Activity) -> DashboardItem:
        present = _PRESENTERS.get(type(activity))
        if present is None:
            raise TypeError(f"unsupported activity: {type(activity).__name__}")
        return present(activity)
