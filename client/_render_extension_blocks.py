# Copyright (c) 2026 Zhambyl Yermagambet
"""Paint an extension's typed terminal view with the pane's own render primitives.

The extension chooses the layout; the pane owns every control sequence. Text
from the extension never reaches the terminal unescaped: a control character is
drawn as a replacement mark, even if the daemon failed to refuse it.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from _render_extension_items import diff_rows, list_rows, status_rows, tree_rows
from _render_extension_table import table_rows
from _render_extension_text import REPLACEMENT as REPLACEMENT, Layout, line_spans, span
from _render_rows import rows

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    import _model_terminal as terminal


def view_rows(
    view: terminal.TerminalViewDocument, width: int, focus: tuple[str, str] | None = None,
) -> list[str]:
    """Paint a whole view: its title, then each block from the top, with the pane focus marked.

    Returns:
        The screen rows of the view.

    """
    layout = Layout(width, focus=focus)
    painted = rows([span(view.title, bold=True)], width)
    for block in view.blocks:
        painted.extend(block_rows(block, layout))
    return painted


def block_rows(block: terminal.BlockDocument, layout: Layout) -> list[str]:
    """Paint one block at the layout's depth.

    Returns:
        The screen rows of the block.

    """
    return PAINTERS[block.kind](block, layout)


def _text_rows(block: terminal.TextBlockDocument, layout: Layout) -> list[str]:
    return layout.wrap(line_spans(block.content))


def _section_rows(block: terminal.SectionBlockDocument, layout: Layout) -> list[str]:
    title = layout.wrap([span(block.title, bold=True)])
    nested = layout.nested()
    return title + [row for child in block.children for row in block_rows(child, nested)]


type Painter = Callable[..., list[str]]
PAINTERS: Mapping[str, Painter] = MappingProxyType({
    "text": _text_rows,
    "section": _section_rows,
    "table": table_rows,
    "file_tree": tree_rows,
    "diff": diff_rows,
    "status": status_rows,
    "list": list_rows,
})
