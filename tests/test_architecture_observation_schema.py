# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep raw table checks active when schema definitions use named constants."""

from pathlib import Path

from tests import architecture_test_controls

METADATA_TABLE_COUNT = 2


def test_raw_table_constant_is_checked() -> None:
    """The table gate must also inspect DDL stored in a separate Python constant."""
    schema_path = Path(__file__).resolve().parents[1] / "repository/impl/sqlite/schema.py"
    source = schema_path.read_text(encoding="utf-8")
    assert source.count("extension_metadata TEXT") == METADATA_TABLE_COUNT
    changed = source.replace("extension_metadata TEXT", "extension_metadata TEXT,\n    value TEXT")
    violations, _table_count = architecture_test_controls.key_value_table_violations(changed)
    assert "raw_events.value is a key-value column" in violations
    assert "canonical_events.value is a key-value column" in violations
