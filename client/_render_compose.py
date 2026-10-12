# Copyright (c) 2026 Zhambyl Yermagambet
"""Split terminal mirror rendering."""

from __future__ import annotations

from _model import (
    SessionModel,
    ShellFold,
)
from _render_feed import (
    FeedPaint,
    MirrorRows,
    scrollback_feed,
)
from _render_rows import (
    _no_link,
    _RowOptions,
    rows,
)
from _render_shells import copy_target
from _render_styles import (
    EMPTY_ENTRY_IDS,
    HEADER_COLOR,
    HEADER_TEXT,
    TRUNCATE_LAYOUT,
    Links,
    Span,
)
from _render_tasks import (
    _rule,
    task_rows,
)
from _render_wrap import screen


def mirror(
    model: SessionModel,
    width: int,
    *,
    copy: Links = _no_link,
    view: Links = _no_link,
    opened: frozenset[str] = EMPTY_ENTRY_IDS,
) -> str:
    """Return the mirror.

    The newest feed that the scrollback keeps, at this width. Called on every frame, on every resize and
        on every expand — a repaint is the only update this surface has.

        The two link builders default to producing nothing, so a caller that wants
        the text and not the clicks (a test, a diff of two paints) gets a clean
        screen without opting out of anything.

    Returns:
        Mirror.

    """
    painted = mirror_rows(model, width, copy=copy, view=view, opened=opened)
    return mirror_screen(painted.rows, width)


def mirror_rows(
    model: SessionModel,
    width: int,
    *,
    copy: Links = _no_link,
    view: Links = _no_link,
    opened: frozenset[str] = EMPTY_ENTRY_IDS,
) -> MirrorRows:
    """Paint the newest feed that the scrollback keeps, and the task panel, without the header.

    Returns:
        The mirror rows.

    """
    feed = scrollback_feed(FeedPaint(model, width, copy, view, opened))
    # The task panel goes LAST, and that is the whole of its placement decision.
    # This surface is newest-at-the-bottom, so the end of the paint is what a
    # reader is actually looking at; and the pane clears its scrollback on every
    # repaint, so there is exactly one copy of it rather than one per frame. The
    # scoreboard would have been the other candidate, but the daemon pins that
    # pane to a fixed height (terminal/adapter.py SCOREBOARD_HEIGHT), and content
    # whose size is decided by a constant in another process is a trap.
    tasks = task_rows(model, width)
    if not tasks:
        return feed
    with_tasks = [*feed.rows, "", _rule(width), *tasks]
    return MirrorRows(with_tasks, feed.shown)


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
