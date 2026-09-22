# Copyright (c) 2026 Zhambyl Yermagambet
"""Test migration and all-or-nothing catalog writes against real SQLite files."""

import sqlite3
from pathlib import Path

import pytest
from pydantic import ValidationError

from extensions.models.catalog import ExtensionCatalogSnapshot, PackageScan
from repository.impl.sqlite import extension_catalog_rows as rows, schema
from repository.impl.sqlite.connection import SqliteDatabase
from tests.extension_host import catalog_fixture as catalog, package_fixture as packages

OLD_VERSION = 26
WIDTH = 50
SCHEMA_FIXTURE = Path(__file__).with_name("fixtures") / "main-schema-26.sql"


def test_upgrade_keeps_populated_old_database(tmp_path: Path) -> None:
    """The current schema adds extension tables without replacing existing application data."""
    previous = _old_database(tmp_path)
    assert previous.schema_version == OLD_VERSION
    upgraded = catalog.repository(tmp_path)
    assert upgraded.read_extension_catalog().revision == 0
    with upgraded.database.read() as connection:
        width = connection.execute(
            "SELECT width_percent FROM pane_widths WHERE working_directory=?", ("/test",),
        ).fetchone()
        version = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
    assert width["width_percent"] == WIDTH
    assert version["version"] == schema.MAIN_SCHEMA_VERSION


def test_failed_catalog_write_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure after package replacement cannot leave partial rows or a new head."""
    packages.write_package(tmp_path / "packages")
    service = catalog.service(tmp_path)
    first = service.rescan_packages(0)
    packages.write_package(tmp_path / "packages", "test.second")
    monkeypatch.setattr(rows, "_retain_manifests", _fail_after_rows)
    with pytest.raises(RuntimeError, match="after catalog rows"):
        service.rescan_packages(first.snapshot.revision)
    assert service.catalog_snapshot() == first.snapshot
    assert catalog.repository(tmp_path).read_extension_catalog() == first.snapshot


def test_duplicate_source_rows_are_rejected(tmp_path: Path) -> None:
    """Duplicate SQL keys fail model validation before a replacement starts."""
    packages.write_package(tmp_path / "packages")
    service = catalog.service(tmp_path)
    scan = service.scanner.scan_packages(service.roots)
    with pytest.raises(ValidationError, match="unique"):
        PackageScan(entries=(*scan.entries, *scan.entries))


def _fail_after_rows(connection: sqlite3.Connection, snapshot: ExtensionCatalogSnapshot) -> None:
    assert connection.in_transaction
    assert snapshot.entries
    message = "test failure after catalog rows"
    raise RuntimeError(message)


def _old_database(directory: Path) -> SqliteDatabase:
    previous_schema = SCHEMA_FIXTURE.read_text(encoding="utf-8")
    previous = SqliteDatabase(str(directory / "main.db"), previous_schema, OLD_VERSION)
    with previous.write() as connection:
        connection.execute("INSERT INTO pane_widths(working_directory, width_percent) VALUES(?, ?)", ("/test", WIDTH))
    return previous
