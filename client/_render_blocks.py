# Copyright (c) 2026 Zhambyl Yermagambet
"""Split terminal mirror rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from _render_rows import (
    _RowOptions,
    rows,
)
from _render_styles import (
    DARK,
    SCROLLBACK_ROWS,
    TEXT_INDENT,
    TRUNCATE_LAYOUT,
    Color,
    Span,
)
from _render_tasks import _rule

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True)
class _BlockContent:
    summary: str
    output: str
    status: str
    finish: list[Span] | None
    links: list[Span] | None = None


def _block(
    chip: str,
    chip_color: Color,
    content: _BlockContent,
    width: int,
) -> list[str]:
    """Return the block.

    The command shape: a chip and what was asked for, then what came back
        behind a rail, then how it ended. Every tool that produces output uses it —
        a search and a shell differ in their chip and in nothing else.

    Returns:
        Block.

    """
    heading = _block_heading(chip, chip_color, content.links)
    heading_rows = rows(heading, width, _RowOptions(mode=TRUNCATE_LAYOUT))
    painted = ["", _rule(width), *heading_rows]
    if content.summary:
        painted.extend(_block_summary_rows(content.summary, width))
    painted.append(_rule(width))
    painted.extend(_block_stream_rows(content, chip_color, width))
    if content.finish is not None:
        painted.append(_rule(width))
        painted.extend(rows(content.finish, width, _RowOptions(mode=TRUNCATE_LAYOUT)))
        painted.append(_rule(width))
    return painted


def _block_summary_rows(summary: str, width: int) -> list[str]:
    return rows(
        [Span(summary)],
        width,
        _RowOptions(continuation=(Span(TEXT_INDENT),)),
    )


def _block_heading(
    chip: str,
    chip_color: Color,
    links: list[Span] | None,
) -> list[Span]:
    heading = [Span(f" {chip} ", DARK, chip_color, bold=True)]
    for index, link in enumerate(links or ()):
        heading.extend((Span(TEXT_INDENT if index == 0 else " "), link))
    return heading


def _block_stream_rows(
    content: _BlockContent,
    chip_color: Color,
    width: int,
) -> list[str]:
    """Paint the newest lines of the status and the output, until the scrollback is full.

    The screen keeps only the newest scrollback rows. Thus an older line has
    no row on the screen, and the paint does not grow with the output.

    Returns:
        The rows of the newest lines, oldest first.

    """
    newest_rows: list[list[str]] = []
    row_count = 0
    rail = (Span("│ ", chip_color),)
    options = _RowOptions(prefix=rail, continuation=rail)
    for line in _newest_lines(content, width):
        if row_count >= SCROLLBACK_ROWS:
            break
        newest_rows.append(rows([Span(line)], width, options))
        row_count += len(newest_rows[-1])
    return [row for line_rows in reversed(newest_rows) for row in line_rows]


def _newest_lines(content: _BlockContent, width: int) -> Iterator[str]:
    """Walk the output and then the status, each from its newest line.

    One long line can fill the scrollback alone. A row holds less than the
    width, so the last scrollback-rows-times-width characters fill the scrollback.

    Yields:
        The end of each line, newest first.

    """
    longest = SCROLLBACK_ROWS * width
    for stream in (content.output, content.status):
        for line in reversed(_tail(stream, SCROLLBACK_ROWS).splitlines()):
            yield line.rstrip()[-longest:]


def _tail(text: str, line_count: int) -> str:
    """Return the end of the text that holds at least its newest lines.

    Each cut is after a line feed. A line feed always ends a line for
    `splitlines`, so the tail has the same newest lines as the full text.

    Returns:
        The tail of the text.

    """
    start = len(text)
    for _ in range(line_count + 1):
        start = text.rfind("\n", 0, start)
        if start < 0:
            return text
    return text[start + 1 :]
