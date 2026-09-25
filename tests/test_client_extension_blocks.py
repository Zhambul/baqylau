# Copyright (c) 2026 Zhambyl Yermagambet
"""Paint typed extension terminal blocks in the pane at narrow, normal, and wide sizes (P07-T01)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests import test_client_loading

terminal = test_client_loading.load_shared("_model_terminal")
blocks = test_client_loading.load_shared("_render_extension_blocks")
width_module = test_client_loading.load_shared("_render_width")

ANSI = re.compile(r"\x1b\[[0-9;]*m|\x1b\]8;;[^\x1b]*\x1b\\")
NARROW = 20
NORMAL = 60
WIDE = 120
FIXTURE = Path(__file__).parent / "client_fixtures" / "terminal_views.json"
VIEWS = json.loads(FIXTURE.read_text(encoding="utf-8"))


def painted(name: str, width: int) -> list[str]:
    """Validate one fixture view as the pane reads it from the daemon, then paint it.

    Returns:
        The visible rows.

    """
    document = terminal.TerminalViewDocument.model_validate_json(json.dumps(VIEWS[name]))
    rows = blocks.view_rows(document, width)
    return [ANSI.sub("", row).rstrip() for row in rows]


@pytest.mark.parametrize("width", [NARROW, NORMAL, WIDE])
def test_every_row_fits_the_pane(width: int) -> None:
    """No painted row is wider than the pane, with wide and combining characters counted by columns."""
    rows = painted("every_block", width)

    assert rows[0] == "Logs"
    assert all(width_module.text_width(row) <= width for row in rows)


@pytest.mark.parametrize("pattern", [
    "Service", "Started 日本語 café", "● Healthy · 3 checks", r"▸ src", "a.py → a.py", "\u203a Second thread",
    r"Name\s+Level", r"api\s+warning",
])
def test_wide_view_shows_every_block(pattern: str) -> None:
    """At a wide size the view shows each block's meaning, and the table's columns align."""
    assert re.search(pattern, "\n".join(painted("every_block", WIDE)))


def test_narrow_table_stacks_its_columns() -> None:
    """A table that does not fit shows one `column: value` line for each cell."""
    rows = painted("narrow_table", NARROW)[1:]
    joined = " ".join(row.strip() for row in rows)

    assert joined.startswith("Repository:")
    assert "baqylau-extension-system" in joined.replace(" ", "")
    assert "Branch: feature" in joined


def test_widths_count_terminal_columns() -> None:
    """A wide character takes two columns, and a combining mark takes none."""
    assert width_module.text_width("日本") == 2 * len("日本")
    assert width_module.text_width("é") == 1
    assert width_module.cut("日本語", 3) == 1


def test_control_characters_are_marked() -> None:
    """An escape sequence in extension text is drawn as a mark, even if the daemon failed to refuse it."""
    document = terminal.TerminalViewDocument.model_validate_json(json.dumps(VIEWS["hostile_text"]))
    raw = "".join(blocks.view_rows(document, NORMAL))

    assert "\x1b[2J" not in raw
    assert "\x07" not in raw
    assert blocks.REPLACEMENT in raw


def test_empty_content_paints_only_what_exists() -> None:
    """An empty view is its title; empty blocks add no rows except a table's header, after the mark lane."""
    assert painted("empty_blocks", NORMAL) == ["Logs", "  Name"]
