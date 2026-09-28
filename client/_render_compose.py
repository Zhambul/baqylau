# Copyright (c) 2026 Zhambyl Yermagambet
"""Split terminal mirror rendering."""

from __future__ import annotations

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
from _render_rows import (
    _no_link,
    _RowOptions,
    rows,
)
from _render_shells import (
    _shell_rows,
    copy_target,
)
from _render_styles import (
    EMPTY_ENTRY_IDS,
    HEADER_COLOR,
    HEADER_TEXT,
    MUTED,
    TRUNCATE_LAYOUT,
    Links,
    Span,
)
from _render_tasks import (
    _rule,
    _said,
    task_rows,
)
from _render_tools import (
    _entry_text,
    _tool_rows,
)
from _render_wrap import screen


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


def mirror(
    model: SessionModel,
    width: int,
    *,
    copy: Links = _no_link,
    view: Links = _no_link,
    opened: frozenset[str] = EMPTY_ENTRY_IDS,
) -> str:
    """Return the mirror.

    The whole feed at this width. Called on every frame, on every resize and
        on every expand — a repaint is the only update this surface has.

        The two link builders default to producing nothing, so a caller that wants
        the text and not the clicks (a test, a diff of two paints) gets a clean
        screen without opting out of anything.

    Returns:
        Mirror.

    """
    return mirror_screen(mirror_rows(model, width, copy=copy, view=view, opened=opened), width)


def mirror_rows(
    model: SessionModel,
    width: int,
    *,
    copy: Links = _no_link,
    view: Links = _no_link,
    opened: frozenset[str] = EMPTY_ENTRY_IDS,
) -> list[str]:
    """Paint the feed and the task panel, without the header.

    Returns:
        The mirror rows.

    """
    lead_actor_id = model.lead_actor_id()
    painted: list[str] = []
    for feed_record in model.feed():
        if isinstance(feed_record, ShellFold):
            painted.extend(
                _shell_rows(
                    feed_record,
                    copy,
                    width,
                    running=model.running_shell(feed_record.shell_id),
                ),
            )
        elif visible(feed_record, lead_actor_id):
            painted.extend(entry_rows(feed_record, model, width, view, opened))
    # The task panel goes LAST, and that is the whole of its placement decision.
    # This surface is newest-at-the-bottom, so the end of the paint is what a
    # reader is actually looking at; and the pane clears its scrollback on every
    # repaint, so there is exactly one copy of it rather than one per frame. The
    # scoreboard would have been the other candidate, but the daemon pins that
    # pane to a fixed height (terminal/adapter.py SCOREBOARD_HEIGHT), and content
    # whose size is decided by a constant in another process is a trap.
    tasks = task_rows(model, width)
    if tasks:
        painted.extend(["", _rule(width), *tasks])
    return painted


def mirror_screen(painted: list[str], width: int, sections: tuple[str, ...] = ()) -> str:
    """Put the header over the mirror rows; extension sections follow the task panel.

    Returns:
        The whole pane.

    """
    if sections:
        painted = [*painted, "", _rule(width), *sections]
    return screen(
        painted,
        rows(
            [Span(HEADER_TEXT, HEADER_COLOR)],
            width,
            _RowOptions(mode=TRUNCATE_LAYOUT),
        ),
    )


def copy_targets(model: SessionModel) -> dict[str, str]:
    """Every copy link's text, for the pane to publish.

    Built from the MODEL rather than collected while painting, so that what a
    click can reach is exactly what the feed holds — a link the paint dropped
    because the screen filled up still copies, and a target for something the
    model never had cannot exist.

    Returns:
        Result mapping.

    """
    targets: dict[str, str] = {}
    for feed_record in model.feed():
        if not isinstance(feed_record, ShellFold):
            continue
        if feed_record.command:
            targets[copy_target(feed_record.shell_id, "cmd")] = feed_record.command
        output = "".join(part for part in (feed_record.status, feed_record.output) if part)
        if output:
            targets[copy_target(feed_record.shell_id, "out")] = output
    return targets
