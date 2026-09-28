# Copyright (c) 2026 Zhambyl Yermagambet
"""Turn extension text into safe, styled spans and a layout for one nesting depth."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

import _render_styles as styles
from _render_rows import _RowOptions, rows

if TYPE_CHECKING:
    import _model_terminal_text as terminal

INDENT = "  "
FOCUS_MARK = "\u203a "
NO_MARK = "  "
REPLACEMENT = "�"
UNSAFE_CATEGORIES = frozenset(("Cc", "Cs"))
KEPT_CONTROLS = frozenset("\n\t")
TONE_COLORS = MappingProxyType({
    "text": styles.TEXT,
    "muted": styles.MUTED,
    "accent": styles.USER,
    "success": styles.SUCCESS,
    "warning": styles.WORKING,
    "error": styles.FAILURE,
})


@dataclass(frozen=True)
class Layout:
    """Keep the pane width, the indent of one nesting depth, and the focused block and item."""

    width: int
    prefix: tuple[styles.Span, ...] = ()
    focus: tuple[str, str] | None = None

    def nested(self) -> Layout:
        """Indent one more level.

        Returns:
            The layout of a child block.

        """
        deeper = (*self.prefix, styles.Span(INDENT))
        return Layout(self.width, deeper, self.focus)

    def selects(self, block_id: str, item_id: str, chosen_by_view: str | None) -> bool:
        """Tell whether an item is selected: by the pane focus in its block, else by the view.

        Returns:
            True for the selected item.

        """
        if self.focus is not None and self.focus[0] == block_id:
            return self.focus[1] == item_id
        return item_id == chosen_by_view

    def mark(self, block_id: str, item_id: str) -> styles.Span:
        """Mark the focused row in the two-column mark lane.

        Returns:
            The mark of the row.

        """
        focused = self.selects(block_id, item_id, None)
        return styles.Span(FOCUS_MARK if focused else NO_MARK, styles.USER, bold=focused)

    def hanging(self) -> _RowOptions:
        """Indent every row after the first one more level.

        Returns:
            The row options of a hanging indent.

        """
        continuation = (*self.prefix, styles.Span(INDENT))
        return _RowOptions(prefix=self.prefix, continuation=continuation)

    def wrap(self, spans: list[styles.Span]) -> list[str]:
        """Wrap spans under this depth's indent.

        Returns:
            The screen rows.

        """
        return rows(spans, self.width, _RowOptions(prefix=self.prefix, continuation=self.prefix))

    def one_row(self, spans: list[styles.Span]) -> list[str]:
        """Keep spans on one row, cut at the pane edge.

        Returns:
            One screen row.

        """
        return rows(spans, self.width, _RowOptions(prefix=self.prefix, mode=styles.TRUNCATE_LAYOUT))


def safe(text: str) -> str:
    """Replace every control character that the pane must not send.

    Returns:
        Text with only printable characters, line breaks, and tabs.

    """
    return "".join(_safe_char(char) for char in text)


def _safe_char(char: str) -> str:
    if char in KEPT_CONTROLS or unicodedata.category(char) not in UNSAFE_CATEGORIES:
        return char
    return REPLACEMENT


def span(text: str, tone: str = "text", *, bold: bool = False) -> styles.Span:
    """Paint one safe run of text in a tone.

    Returns:
        The styled span.

    """
    return styles.Span(safe(text), TONE_COLORS[tone], bold=bold)


def line_spans(line: terminal.TextLineDocument) -> list[styles.Span]:
    """Paint one line of styled runs.

    Returns:
        The spans with their tone colors.

    """
    return [span(run.text, run.tone, bold=run.bold) for run in line.spans]
