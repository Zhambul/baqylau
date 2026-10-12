# Copyright (c) 2026 Zhambyl Yermagambet
"""Paint the newest feed that the mirror's scrollback keeps."""

from __future__ import annotations

from dataclasses import dataclass

from _model import (
    EntryRecord,
    SessionModel,
    ShellFold,
)
from _render_assignments import (
    _assignment_rows,
    visible,
)
from _render_entries import (
    _attention_rows,
    _message_rows,
    _note_rows,
)
from _render_files import _file_rows
from _render_shells import _shell_rows
from _render_styles import (
    MUTED,
    SCROLLBACK_ROWS,
    Links,
)
from _render_tasks import _said
from _render_tools import (
    _entry_text,
    _tool_rows,
)


def entry_rows(
    entry: EntryRecord,
    model: SessionModel,
    width: int,
    view: Links,
    opened: frozenset[str],
) -> list[str]:
    kind = entry.type
    primary_rows = _primary_entry_rows(entry, model, width, view, opened)
    if primary_rows is not None:
        return primary_rows
    if kind in {
        "search",
        "web",
        "browser",
        "worktree",
        "skill_started",
        "skill_finished",
    }:
        return _tool_rows(entry, width)
    if kind in {"question_asked", "question_answered", "plan_proposed", "plan_resolved"}:
        return _attention_rows(entry, width)
    if kind in {"assignment_started", "assignment_finished"}:
        return _assignment_rows(entry, width)
    if kind in {
        "compaction_started",
        "compaction_finished",
        "model_change",
        "effort_change",
        "extension",
    }:
        rendered = _note_rows(entry, width)
    else:
        # Turn markers and unknown grouping facts do not draw a line.
        rendered = []
    return rendered


def _primary_entry_rows(
    entry: EntryRecord,
    model: SessionModel,
    width: int,
    view: Links,
    opened: frozenset[str],
) -> list[str] | None:
    if entry.type == "message":
        return _message_rows(entry, model, width)
    if entry.type == "reasoning":
        return _said("THINK", _entry_text(entry.body.content), width, MUTED)
    if entry.type == "file":
        return _file_rows(
            entry,
            view,
            width,
            open_now=entry.entry_id in opened,
        )
    return None


@dataclass(frozen=True)
class MirrorRows:
    """The feed rows that the scrollback keeps, then the task panel."""

    rows: list[str]
    # How many of the newest feed items the rows show. An older item has no row in the scrollback.
    shown: int


@dataclass(frozen=True)
class FeedPaint:
    """What one paint of the feed needs."""

    model: SessionModel
    width: int
    copy: Links
    view: Links
    opened: frozenset[str]

    def record_rows(self, feed_record: EntryRecord | ShellFold) -> list[str]:
        if isinstance(feed_record, ShellFold):
            running = self.model.running_shell(feed_record.shell_id)
            return _shell_rows(feed_record, self.copy, self.width, running=running)
        if visible(feed_record, self.model.lead_actor_id()):
            return entry_rows(feed_record, self.model, self.width, self.view, self.opened)
        return []


def scrollback_feed(feed_paint: FeedPaint) -> MirrorRows:
    """Paint from the newest item to the oldest, and stop when the scrollback is full.

    Then the cost of a paint does not grow with the session.

    Returns:
        The feed rows, oldest first.

    """
    newest_rows: list[list[str]] = []
    row_count = 0
    for feed_record in feed_paint.model.newest_feed():
        if row_count >= SCROLLBACK_ROWS:
            break
        newest_rows.append(feed_paint.record_rows(feed_record))
        row_count += len(newest_rows[-1])
    oldest_first = [row for record_rows in reversed(newest_rows) for row in record_rows]
    return MirrorRows(oldest_first, len(newest_rows))
