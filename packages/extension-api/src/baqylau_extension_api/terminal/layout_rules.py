# Copyright (c) 2026 Zhambyl Yermagambet
"""Check bounded terminal layout identities and action references."""

from collections.abc import Iterator

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.rules import require_unique
from baqylau_extension_api.terminal.blocks import ListBlock, ListItem, TableBlock, TableRow
from baqylau_extension_api.terminal.files import FileTreeBlock, FileTreeItem
from baqylau_extension_api.terminal.layout import SectionBlock, TerminalBlock

MAX_LAYOUT_DEPTH = 8
MAX_LAYOUT_BLOCKS = 1024


def walk_blocks(blocks: tuple[TerminalBlock, ...]) -> Iterator[TerminalBlock]:
    """Visit each block once with fixed depth and count bounds.

    Yields:
        Blocks in display order.

    Raises:
        ExtensionContractError: If the section tree exceeds a host bound.

    """
    for count, block in enumerate(_walk_sections(blocks, 0), start=1):
        if count > MAX_LAYOUT_BLOCKS:
            message = "terminal layout exceeds its block limit"
            raise ExtensionContractError(message)
        yield block


def _walk_sections(blocks: tuple[TerminalBlock, ...], depth: int) -> Iterator[TerminalBlock]:
    if depth > MAX_LAYOUT_DEPTH:
        message = "terminal layout exceeds its depth limit"
        raise ExtensionContractError(message)
    for block in blocks:
        yield block
        if isinstance(block, SectionBlock) and block.children:
            yield from _walk_sections(block.children, depth + 1)


def validate_layout(blocks: tuple[TerminalBlock, ...], action_ids: frozenset[str]) -> None:
    """Require unique block IDs and valid local row and action references."""
    flattened = tuple(walk_blocks(blocks))
    require_unique((block.block_id for block in flattened), "terminal block IDs")
    for block in flattened:
        _validate_entries(block, action_ids)


def _block_entries(block: TerminalBlock) -> tuple[ListItem | TableRow | FileTreeItem, ...]:
    if isinstance(block, ListBlock):
        return block.entries
    if isinstance(block, TableBlock):
        return block.rows
    if isinstance(block, FileTreeBlock):
        return block.nodes
    return ()


def _validate_entries(block: TerminalBlock, action_ids: frozenset[str]) -> None:
    entries = _block_entries(block)
    require_unique((entry.item_id for entry in entries), "terminal row IDs")
    for entry in entries:
        if entry.action_id is not None and entry.action_id not in action_ids:
            message = "terminal row refers to an undeclared action"
            raise ExtensionContractError(message)
    if (
        isinstance(block, ListBlock)
        and block.selected_id is not None
        and block.selected_id not in {row.item_id for row in entries}
    ):
        message = "terminal selection refers to an absent list entry"
        raise ExtensionContractError(message)
