# Copyright (c) 2026 Zhambyl Yermagambet
"""Convert stored records into a new generation before activation, and keep the old rows on failure (C04, C14)."""

from __future__ import annotations

from contextlib import closing
from typing import TYPE_CHECKING

from tests.extension_host import (
    catalog_fixture,
    lifecycle_control_fixture as controls,
    migration_host_fixture as fixture,
    record_migration_fixture as records,
)

if TYPE_CHECKING:
    from pathlib import Path

    from repository.impl.sqlite.connection import SqliteDatabase

RETIRED = f"default:{records.OWNER}"
CONVERTED = records.StoredRow(
    schema_version=2, document='{"title":"First"}', summary="Converted value.", revision=records.REVISION,
)


def reloaded(directory: Path, wheels: Path, labels: tuple[str, ...], outcome: str = "published") -> SqliteDatabase:
    """Enable version one, store its records, then reload version two with a record conversion.

    Returns:
        The main database after the reload.

    """
    source = fixture.write_package(directory, wheels)
    database = catalog_fixture.repository(directory).database
    with closing(controls.open_control(directory)) as case:
        fixture.activate(case)
        records.seed(database, labels)
        fixture.select_version(source, 2)
        case.rescan()
        request = case.request("reload", "record-upgrade", fixture.OWNER)
        assert case.control.change_lifecycle(fixture.OWNER, request).status == "accepted"
        case.host.finish(outcome)
    return database


def test_upgrade_converts_every_live_record(tmp_path: Path, runtime_wheels: Path) -> None:
    """Each stored row has the new schema and value, at its old revision."""
    database = reloaded(tmp_path, runtime_wheels, ("First", "Second"))

    live = records.live_rows(database)
    assert live[0] == CONVERTED
    assert [row.schema_version for row in live] == [2, 2]


def test_upgrade_switches_the_head(tmp_path: Path, runtime_wheels: Path) -> None:
    """The converted generation is live with the copied cursor; the old generation keeps version-one rows."""
    database = reloaded(tmp_path, runtime_wheels, ("First",))

    states = records.generations(database)
    active = next(iter(set(states) - {RETIRED}))
    assert states == {active: "active", RETIRED: "retired"}
    assert records.cursor(database, active) == records.REVISION
    assert [row.schema_version for row in records.generation_rows(database, RETIRED)] == [1]


def test_rejected_conversion_keeps_the_live_rows(tmp_path: Path, runtime_wheels: Path) -> None:
    """A failed batch fails the reload and the migrating generation; the old rows stay live."""
    database = reloaded(tmp_path, runtime_wheels, ("reject",), "failed")

    assert [row.schema_version for row in records.live_rows(database)] == [1]
    states = records.generations(database)
    assert list(states.values()) == ["failed"]
    assert not records.generation_rows(database, next(iter(states)))
