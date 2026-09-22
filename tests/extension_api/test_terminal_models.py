# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid display data before a client can render it."""

import pytest
from baqylau_extension_api.terminal.blocks import TableBlock, TableRow
from baqylau_extension_api.terminal.files import FileTreeBlock, FileTreeItem
from baqylau_extension_api.terminal.models import TerminalAction, TerminalView, TerminalViewport
from baqylau_extension_api.terminal.text import TextSpan
from pydantic import ValidationError

from tests.extension_api import samples, terminal_example, terminal_samples


@pytest.mark.parametrize("text", [
    "\x00", "\x1b[2J", "\x7f", "\x9b31m", "\r", "\b", "\u202e", "\u2066", "\ud800",
])
def test_display_text_rejects_terminal_controls(text: str) -> None:
    """Keep control sequences and directional overrides out of display text."""
    with pytest.raises(ValidationError, match=r"terminal display text|valid string"):
        TextSpan(text=text)


def test_display_text_keeps_visible_unicode() -> None:
    """Preserve user text that the client must measure and wrap."""
    text = "Қазақ\t日本語\n🙂 e\u0301"
    span = TextSpan(text=text, tone="accent", bold=True)
    assert TextSpan.model_validate_json(span.model_dump_json()) == span


@pytest.mark.parametrize("columns", [0, -1, 513, "80", True])
def test_viewport_requires_bounded_cell_counts(columns: object) -> None:
    """Reject missing space, excessive dimensions, and coerced numbers."""
    with pytest.raises(ValidationError):
        TerminalViewport.model_validate({"columns": columns, "rows": 24})


def test_table_rows_match_the_declared_columns() -> None:
    """Do not let a malformed row shift the meaning of table cells."""
    row = TableRow(item_id="row", cells=(terminal_example.line("One"),))
    with pytest.raises(ValidationError, match="declared columns"):
        TableBlock(block_id="table", columns=("First", "Second"), rows=(row,))


@pytest.mark.parametrize("nodes", [
    (FileTreeItem(item_id="orphan", label="file", depth=1),),
    (
        FileTreeItem(item_id="directory", label="src", node_kind="directory"),
        FileTreeItem(item_id="skipped", label="Skipped", depth=2),
    ),
    (
        FileTreeItem(item_id="file", label="Parent"),
        FileTreeItem(item_id="child", label="Child", depth=1),
    ),
])
def test_file_tree_requires_directory_parents(nodes: tuple[FileTreeItem, ...]) -> None:
    """Require each visible tree row to have a valid preceding parent."""
    with pytest.raises(ValidationError, match="directory parent"):
        FileTreeBlock(block_id="tree", nodes=nodes)


def test_actions_do_not_accept_shell_commands() -> None:
    """Allow typed action references, never a shell string for the client."""
    action = TerminalAction(
        action_id="open", command_id="test.reader.open", label="Open", arguments=samples.encoded_document(),
    )
    with pytest.raises(ValidationError, match="Extra inputs"):
        TerminalAction.model_validate({**action.model_dump(), "shell": "echo unsafe"})


def test_layout_round_trip_covers_all_block_types() -> None:
    """Keep layout, Unicode, and exact binding fields through the wire codec."""
    request = terminal_samples.view_request()
    view = terminal_example.TerminalExample(samples.worker_environment().extension_info).present(request)
    assert TerminalView.model_validate_json(view.model_dump_json()) == view
