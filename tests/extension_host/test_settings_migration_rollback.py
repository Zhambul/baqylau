# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid converted data and roll back a failed SQLite commit."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from repository.impl.sqlite.connection import SqliteDatabase
from tests import storage_reads
from tests.extension_host import (
    lifecycle_fixture as lifecycle,
    migration_result_fixture,
    migration_store_fixture as fixture,
    runtime_commit_fixture as commits,
)


def test_migration_commit_failure_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The old head, raw settings, resolution, and operation survive a failed COMMIT."""
    case = fixture.migration_store(tmp_path)
    completion = lifecycle.completion(case.admit())
    completion = completion.model_copy(update={"resolution": case.resolution})
    before = case.store.read_extension_lifecycle()
    with closing(commits.fault_connection(case.store.database)) as connection:
        monkeypatch.setattr(SqliteDatabase, "_thread_connection", lambda _database: connection)
        with pytest.raises(sqlite3.OperationalError, match="injected COMMIT"):
            case.store.finish_extension_operation(completion)
        assert case.store.read_extension_lifecycle() == before
        assert storage_reads.read_extension_runtime(
            case.store, case.proposal.candidate.runtime_revision,
        ) == case.proposal.candidate
        assert case.store.finish_extension_operation(completion).accepted
        assert case.store.read_extension_lifecycle().committed_runtime == case.resolution.runtime


def test_invalid_converted_document_is_not_saved(tmp_path: Path) -> None:
    """A reply with valid identities cannot supply inconsistent raw and effective values."""
    case = fixture.migration_store(tmp_path)
    admitted = case.admit()
    change = case.resolution.settings_changes[0]
    assert change.settings.installation is not None
    changed = change.model_copy(update={"settings": change.settings.model_copy(update={
        "installation": change.settings.installation.model_copy(update={"json_text": '{"title":42}'}),
    })})
    resolution = case.resolution.model_copy(update={"settings_changes": (changed,)})
    with pytest.raises(ValueError, match=r"settings|schema"):
        case.store.finish_extension_operation(lifecycle.completion(admitted).model_copy(update={
            "resolution": resolution,
        }))
    assert case.store.read_extension_lifecycle().settings[0].settings == fixture.overrides(1)


def test_target_schema_is_checked_at_commit(tmp_path: Path) -> None:
    """Even consistent raw and effective output must satisfy the registered schema."""
    case = fixture.migration_store(tmp_path)
    completion = lifecycle.completion(case.admit())
    with pytest.raises(ValueError, match="not of type"):
        case.store.finish_extension_operation(completion.model_copy(update={
            "resolution": migration_result_fixture.invalid_target(case),
        }))
    assert case.store.read_extension_lifecycle().settings[0].settings == fixture.overrides(1)
