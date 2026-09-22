# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep old migration tests independent of later production table definitions."""

from pathlib import Path

from repository.impl.sqlite.connection import SqliteDatabase

BASELINE_VERSION = 26


def baseline_database(path: str) -> SqliteDatabase:
    """Load saved schema 26 before a test restores its earlier target fields.

    Returns:
        An unopened database with the original unversioned fact tables.

    """
    baseline = Path(__file__).parent / "extension_host/fixtures/main-schema-26.sql"
    return SqliteDatabase(path, baseline.read_text(encoding="utf-8"), BASELINE_VERSION)
