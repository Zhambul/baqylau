# Copyright (c) 2026 Zhambyl Yermagambet
"""Upgrade an independent populated schema-27 file without losing catalog history."""

from pathlib import Path

from extensions.configuration import ExtensionRoots
from extensions.discovery import FilesystemExtensionScanner
from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.extension_catalog import SqliteExtensionCatalogRepository
from repository.impl.sqlite.schema import MAIN_SCHEMA_VERSION
from tests.extension_host import lifecycle_fixture as fixtures, package_fixture

PREVIOUS_VERSION = 27
WIDTH = 55
FIXTURES = Path(__file__).with_name("fixtures")


def test_populated_previous_schema_upgrade(tmp_path: Path) -> None:
    """Retained declarations and unrelated settings survive the new management tables."""
    previous = _previous_database(tmp_path)
    package_fixture.write_package(tmp_path / "packages")
    scan = FilesystemExtensionScanner().scan_packages(ExtensionRoots((tmp_path / "packages",)))
    catalog = SqliteExtensionCatalogRepository(previous).replace_extension_catalog(0, scan)
    upgraded = fixtures.repository(tmp_path)
    assert upgraded.read_extension_lifecycle().revision == 0
    assert SqliteExtensionCatalogRepository(upgraded.database).read_extension_catalog() == catalog.snapshot
    _assert_retained(upgraded.database)


def _assert_retained(database: SqliteDatabase) -> None:
    with database.read() as connection:
        version = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        width = connection.execute("SELECT width_percent FROM pane_widths WHERE working_directory='/test'").fetchone()
        retained = connection.execute("SELECT manifest FROM extension_package_manifests").fetchone()
    assert version["version"] == MAIN_SCHEMA_VERSION
    assert width["width_percent"] == WIDTH
    assert retained is not None


def _previous_database(directory: Path) -> SqliteDatabase:
    text = (FIXTURES / "main-schema-26.sql").read_text(encoding="utf-8")
    catalog = (FIXTURES / "extension-catalog-schema-27.sql").read_text(encoding="utf-8")
    previous = SqliteDatabase(str(directory / "main.db"), f"{text}\n{catalog}", PREVIOUS_VERSION)
    with previous.write() as connection:
        connection.execute("INSERT INTO pane_widths(working_directory, width_percent) VALUES('/test', ?)", (WIDTH,))
    return previous
