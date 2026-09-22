# Copyright (c) 2026 Zhambyl Yermagambet
"""Upgrade a populated independent schema-28 database without changing prior state."""

from pathlib import Path

from extensions.models.lifecycle_operations import LifecycleProposal
from extensions.models.lifecycle_selection import RuntimeSelection
from extensions.models.settings import SettingsOverrides
from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.schema import MAIN_SCHEMA_VERSION
from tests.extension_host import lifecycle_fixture

PREVIOUS_VERSION = 28
FIXTURES = Path(__file__).with_name("fixtures")
REVISION = 3


def test_schema_upgrade_retains_prior_runtime(tmp_path: Path) -> None:
    """Old complete selections remain valid without a migration resolution."""
    previous = _database(tmp_path)
    proposal = _seed(previous)
    upgraded = lifecycle_fixture.repository(tmp_path)
    state = upgraded.read_extension_lifecycle()
    assert state.revision == REVISION and state.committed_runtime == proposal.candidate
    assert state.settings[0].settings == SettingsOverrides(revision=1)
    operation = upgraded.read_extension_operation(proposal.operation_id)
    assert operation is not None and operation.resolution is None
    assert operation.proposal == proposal and operation.status == "succeeded"
    _require_schema(upgraded.database)


def _database(directory: Path) -> SqliteDatabase:
    names = (
        "main-schema-26.sql", "extension-catalog-schema-27.sql", "extension-lifecycle-schema-28.sql",
    )
    paths = tuple(FIXTURES / name for name in names)
    content = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    return SqliteDatabase(str(directory / "main.db"), content, PREVIOUS_VERSION)


def _seed(database: SqliteDatabase) -> LifecycleProposal:
    proposal = LifecycleProposal(
        operation_id="old-operation", manager_id="old-manager", expected_revision=1,
        kind="restore", candidate=RuntimeSelection(runtime_revision="old-runtime", catalog_revision=0),
    )
    with database.write() as connection:
        connection.execute(
            "INSERT INTO extension_runtime_revisions VALUES(?, ?, ?)",
            (proposal.candidate.runtime_revision, proposal.candidate.model_dump_json(), lifecycle_fixture.NOW),
        )
        connection.execute(
            "INSERT INTO extension_lifecycle_operations VALUES(?, ?, ?, 2, 'succeeded', ?, ?, ?, NULL)",
            (proposal.operation_id, proposal.manager_id, proposal.candidate.runtime_revision,
             lifecycle_fixture.NOW, lifecycle_fixture.NOW, proposal.model_dump_json(exclude_none=True)),
        )
        connection.execute(
            "INSERT INTO extension_lifecycle_head VALUES(1, ?, ?, ?, NULL)",
            (REVISION, proposal.manager_id, proposal.candidate.runtime_revision),
        )
        connection.execute("INSERT INTO extension_settings VALUES('saved-owner', ?)", (
            SettingsOverrides(revision=1).model_dump_json(),
        ))
    return proposal


def _require_schema(database: SqliteDatabase) -> None:
    with database.read() as connection:
        row = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        runtime = connection.execute("SELECT resolution FROM extension_runtime_resolutions").fetchone()
    assert row["version"] == MAIN_SCHEMA_VERSION
    assert runtime is None
