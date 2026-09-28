# Copyright (c) 2026 Zhambyl Yermagambet
"""The mirror shows a stored extension entry without extension code (P07-T04)."""

from __future__ import annotations

import re

from tests import test_client_loading

compose = test_client_loading.load_shared("_render_compose")
entries = test_client_loading.load_shared("_model_entry")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
WIDTH = 60
ENTRY_JSON = (
    '{"entry_id":"e1","type":"extension","cursor":1,"actor_id":"lead","occurred_at":1.0,"summary":%s,'
    '"body":{"owner":"test.git","entry_type":"commit","source_event_id":"s1","document":"{}",'
    '"schema_ref":{"owner":"test.git","name":"commit","version":1,"digest":"d"}}}'
)


def _painted(summary: str) -> list[str]:
    entry = entries.EntryRecord.model_validate_json(ENTRY_JSON % summary)
    rows = compose.entry_rows(entry, None, WIDTH, None, frozenset())
    return [ANSI.sub("", row).rstrip() for row in rows]


def test_entry_shows_its_summary() -> None:
    """The stored summary is the line; the extension does not need to run."""
    assert _painted('"Committed 3 files"') == ["⟳ Committed 3 files"]


def test_entry_without_summary_names_its_type() -> None:
    """An entry without a summary still has one line that names its owner and type."""
    assert _painted("null") == ["⟳ test.git: commit"]
