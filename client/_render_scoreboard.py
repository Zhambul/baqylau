# Copyright (c) 2026 Zhambyl Yermagambet
"""Split terminal mirror rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from _render_numbers import duration
from _render_rows import (
    _RowOptions,
    rows,
)
from _render_score_details import (
    _score_detail,
    _score_usage,
)
from _render_statistics import (
    SessionStatistics,
    session_statistics,
    session_usage,
)
from _render_styles import (
    FAILURE,
    MUTED,
    PRIMARY_TEXT,
    SEPARATOR,
    TRUNCATE_LAYOUT,
    Span,
    _ScorePart,
)
from _render_wrap import screen

if TYPE_CHECKING:
    from _model import (
        SessionModel,
    )


def _row(marker: str, parts: list[_ScorePart], width: int) -> list[str]:
    """One scoreboard row: a marker, then as many ` · `-joined parts as fit.

    Parts are dropped from the RIGHT when they do not fit, never truncated: the
    row is ordered most important first, and half a number is worse than none.

    Returns:
        Result items.

    """
    prefix = f" {marker} "
    available = max(0, width - len(prefix))
    visible = _visible_score_parts(parts, available)
    content = _score_spans(visible)
    return rows(
        content,
        width,
        _RowOptions(
            prefix=(Span(prefix, dim=True),),
            mode=TRUNCATE_LAYOUT,
        ),
    )


def _visible_score_parts(
    parts: list[_ScorePart],
    available: int,
) -> list[_ScorePart]:
    visible = list(parts)
    while visible and _score_width(visible) > available:
        visible.pop()
    return visible


def _score_width(parts: list[_ScorePart]) -> int:
    text_width = sum(len(part.text) for part in parts)
    return text_width + len(SEPARATOR) * (len(parts) - 1)


def _score_spans(visible: list[_ScorePart]) -> list[Span]:
    content: list[Span] = []
    for index, (text, color) in enumerate(visible):
        if index:
            content.append(Span(SEPARATOR, dim=True))
        content.append(Span(text, color))
    return content


def scoreboard(model: SessionModel, width: int, chips: tuple[_ScorePart, ...] = ()) -> str:
    """Return the scoreboard.

    The five status rows: who, how much said, what was done, what it cost.

    Returns:
        Scoreboard.

    """
    statistics = session_statistics(model)
    tokens, cost = session_usage(model)
    painted = [
        *_row("⬡", _score_identity(model), width),
        *_row("✉", [_ScorePart(f"{int(statistics.actor_message_count)} msgs", MUTED)], width),
        *_row("▪", _score_activity(statistics), width),
        *_row("Σ", _score_usage(tokens, cost), width),
        *_row(" ", [*_score_detail(statistics), *chips], width),
    ]
    return screen(painted)


def _score_identity(model: SessionModel) -> list[_ScorePart]:
    identity = [_ScorePart(model.session.session_id, PRIMARY_TEXT)]
    account = model.session.account
    if account:
        identity.append(_ScorePart(f"◈ {account.display_name}", MUTED))
    return identity


def _score_activity(statistics: SessionStatistics) -> list[_ScorePart]:
    activity = [_ScorePart(f"{int(statistics.shell_command_count)} cmds", MUTED)]
    if statistics.failed_shell_command_count:
        activity.append(_ScorePart(f"{int(statistics.failed_shell_command_count)}✗", FAILURE))
    activity.append(_ScorePart(f"⏱ {duration(statistics.active_seconds)}", MUTED))
    return activity
