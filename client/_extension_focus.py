# Copyright (c) 2026 Zhambyl Yermagambet
"""Find the items of a view that can take focus, and move the focus between them."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import _model_terminal as terminal

MOVES = frozenset(("up", "down"))


@dataclass(frozen=True)
class FocusItem:
    """Name one focusable item and the action that Enter runs on it, if any."""

    block_id: str
    item_id: str
    action_id: str | None = None


def focus_items(view: terminal.TerminalViewDocument) -> tuple[FocusItem, ...]:
    """List the list entries, tree nodes, and table rows of a view in paint order.

    Returns:
        The focusable items.

    """
    found: list[FocusItem] = []
    for block in view.blocks:
        found.extend(_block_items(block))
    return tuple(found)


def moved(
    focusable: tuple[FocusItem, ...], current: FocusItem | None, key: str,
) -> FocusItem | None:
    """Move the focus one item, and keep it on an item that still exists.

    Returns:
        The new focus, or None for a view without items.

    """
    if not focusable:
        return None
    position = _position(focusable, current)
    if key in MOVES:
        step = -1 if key == "up" else 1
        last = len(focusable) - 1
        position = min(max(position + step, 0), last)
    return focusable[position]


def _position(focusable: tuple[FocusItem, ...], current: FocusItem | None) -> int:
    identities = [(candidate.block_id, candidate.item_id) for candidate in focusable]
    if current is None or (current.block_id, current.item_id) not in identities:
        return 0
    return identities.index((current.block_id, current.item_id))


def _block_items(block: terminal.BlockDocument) -> list[FocusItem]:
    if block.kind == "section":
        return [candidate for child in block.children for candidate in _block_items(child)]
    if block.kind == "list":
        return [FocusItem(block.block_id, entry.item_id, entry.action_id) for entry in block.entries]
    if block.kind == "file_tree":
        return [FocusItem(block.block_id, node.item_id, node.action_id) for node in block.nodes]
    if block.kind == "table":
        return [FocusItem(block.block_id, row.item_id, row.action_id) for row in block.rows]
    return []
