# Copyright (c) 2026 Zhambyl Yermagambet
"""Paint extension file trees, lists, status lines, and diffs."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

import _render_styles as styles
from _render_diff_paint import _diff_content_rows
from _render_extension_text import Layout, line_spans, safe, span
from _render_rows import _RowOptions, rows

if TYPE_CHECKING:
    import _model_terminal as terminal
    import _model_terminal_items as terminal_items

SELECTED_MARK = "\u203a "
UNSELECTED_MARK = "  "
NODE_MARKS = MappingProxyType({"directory": "▸ ", "file": "· "})
PATH_ARROW = " → "


def tree_rows(block: terminal_items.FileTreeBlockDocument, layout: Layout) -> list[str]:
    """Paint one row for each node, indented by its depth.

    Returns:
        The tree rows; a long path is cut at the pane edge.

    """
    return [row for node in block.nodes for row in _node_rows(block.block_id, node, layout)]


def _node_rows(block_id: str, node: terminal_items.FileTreeItemDocument, layout: Layout) -> list[str]:
    indent = span(_indent(node.depth) + NODE_MARKS[node.node_kind], "muted")
    label = span(node.label, node.tone)
    return layout.one_row([layout.mark(block_id, node.item_id), indent, label])


def _indent(depth: int) -> str:
    return "  " * depth


def list_rows(block: terminal_items.ListBlockDocument, layout: Layout) -> list[str]:
    """Paint each entry with a mark for the selected one, and its detail below it.

    Returns:
        The list rows.

    """
    return [row for entry in block.entries for row in _entry_rows(block, entry, layout)]


def _entry_rows(
    block: terminal_items.ListBlockDocument, entry: terminal_items.ListItemDocument, layout: Layout,
) -> list[str]:
    selected = layout.selects(block.block_id, entry.item_id, block.selected_id)
    mark = styles.Span(SELECTED_MARK if selected else UNSELECTED_MARK, styles.USER, bold=selected)
    continuation = (*layout.prefix, styles.Span(UNSELECTED_MARK))
    options = _RowOptions(prefix=layout.prefix, continuation=continuation)
    painted = rows([mark, *line_spans(entry.label)], layout.width, options)
    if entry.detail is None:
        return painted
    return painted + Layout(layout.width, continuation).wrap(line_spans(entry.detail))


def status_rows(block: terminal.StatusBlockDocument, layout: Layout) -> list[str]:
    """Paint a status line: a tone mark, the label, and its detail.

    Returns:
        One status row.

    """
    mark = span("● ", block.tone)
    spans = [mark, span(block.label, block.tone, bold=True)]
    if block.detail is not None:
        separator = styles.Span(styles.SEPARATOR, styles.DIM)
        spans += [separator, span(block.detail.text, "muted")]
    return layout.one_row(spans)


def diff_rows(block: terminal.DiffBlockDocument, layout: Layout) -> list[str]:
    """Paint the paths, then the diff with the pane's diff painter.

    Returns:
        The diff rows.

    """
    body = _diff_content_rows(safe(block.unified_diff), layout.width)
    paths = [path for path in (block.old_path, block.new_path) if path]
    if not paths:
        return body
    return layout.wrap([span(PATH_ARROW.join(paths), "muted")]) + body
