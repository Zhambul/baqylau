# Copyright (c) 2026 Zhambyl Yermagambet
"""Align an extension table when its columns fit, else show each cell on its own line."""

from __future__ import annotations

from typing import TYPE_CHECKING

import _render_styles as styles
from _render_extension_text import NO_MARK, Layout, line_spans, span
from _render_rows import _RowOptions, rows
from _render_span_operations import _take

if TYPE_CHECKING:
    import _model_terminal_items as terminal

COLUMN_GAP = "  "
type Cells = list[list[styles.Span]]


def table_rows(block: terminal.TableBlockDocument, layout: Layout) -> list[str]:
    """Paint the header and rows of one table.

    Returns:
        Aligned rows when the natural column widths fit, else stacked rows.

    """
    header: Cells = [[span(column, "muted", bold=True)] for column in block.columns]
    body = [[line_spans(cell) for cell in row.cells] for row in block.rows]
    widths = [_column_width(header, body, index) for index in range(len(header))]
    if not _fits(widths, layout):
        return [row for cells in body for row in _stacked(block.columns, cells, layout)]
    header_row = _aligned_row(styles.Span(NO_MARK), header, widths, layout)
    return [header_row, *_body_rows(block, body, widths, layout)]


def _body_rows(
    block: terminal.TableBlockDocument, body: list[Cells], widths: list[int], layout: Layout,
) -> list[str]:
    painted: list[str] = []
    for row, cells in zip(block.rows, body, strict=True):
        mark = layout.mark(block.block_id, row.item_id)
        painted.append(_aligned_row(mark, cells, widths, layout))
    return painted


def _fits(widths: list[int], layout: Layout) -> bool:
    available = layout.width - styles.spans_width(layout.prefix)
    gaps = len(COLUMN_GAP) * (len(widths) - 1)
    return sum(widths) + gaps + len(NO_MARK) <= available


def _column_width(header: Cells, body: list[Cells], index: int) -> int:
    present = [row[index] for row in body if index < len(row)]
    widths = [styles.spans_width(cell) for cell in present]
    return max([styles.spans_width(header[index]), *widths])


def _aligned_row(mark: styles.Span, cells: Cells, widths: list[int], layout: Layout) -> str:
    spans = [*layout.prefix, mark]
    for index, column_width in enumerate(widths):
        cell = cells[index] if index < len(cells) else []
        last = index == len(widths) - 1
        spans.extend(_padded(cell, column_width, last=last))
    return rows(spans, layout.width, _RowOptions(mode=styles.TRUNCATE_LAYOUT))[0]


def _padded(cell: list[styles.Span], column_width: int, *, last: bool) -> list[styles.Span]:
    taken = _take(cell, column_width).taken
    if last:
        return taken
    padding = column_width - styles.spans_width(taken)
    return [*taken, styles.Span(" " * padding + COLUMN_GAP)]


def _stacked(columns: tuple[str, ...], cells: Cells, layout: Layout) -> list[str]:
    painted: list[str] = []
    options = layout.hanging()
    for column, cell in zip(columns, cells, strict=False):
        label = span(f"{column}: ", "muted")
        painted.extend(rows([label, *cell], layout.width, options))
    return painted
