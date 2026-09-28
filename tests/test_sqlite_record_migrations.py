# Copyright (c) 2026 Zhambyl Yermagambet
"""Copy an owner's live rows into a migrating generation, replace converted rows, and discard a stopped copy."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.models.record_changes import PutRecord
from baqylau_extension_api.models.scopes import InstallationScope

from extensions.models.lifecycle_state import ManagerClaim
from extensions.models.record_migration import RecordSource
from repository.impl.sqlite.extension_lifecycle import SqliteExtensionLifecycleRepository
from repository.impl.sqlite.record_migrations import SqliteRecordMigrationStore
from tests.extension_api import migration_samples
from tests.extension_host import record_migration_fixture as records

if TYPE_CHECKING:
    from repository.impl.sqlite.connection import SqliteDatabase

SOURCE = RecordSource(collection=records.COLLECTION, source_schema=migration_samples.schema(1).reference)
LIMIT = 10
OLD, NEW = 1, 2
LABELS = ("First", "Second")


def test_start_copies_rows_and_cursors(main: SqliteDatabase) -> None:
    """The migrating generation has the live rows and cursor; the live rows do not change."""
    records.seed(main, LABELS)
    store = SqliteRecordMigrationStore(main)

    migrating = store.start(records.OWNER)

    assert records.generations(main) == {migrating.generation: "migrating"}
    assert records.generation_rows(main, migrating.generation) == records.live_rows(main)
    assert records.cursor(main, migrating.generation) == records.REVISION


def test_stale_page_reads_one_scope(main: SqliteDatabase) -> None:
    """The stale rows of one scope come in key order with the scope's copied cursor."""
    records.seed(main, LABELS)
    store = SqliteRecordMigrationStore(main)
    migrating = store.start(records.OWNER)

    assert store.stale_scopes(migrating, SOURCE) == (InstallationScope(),)
    page = store.stale_page(migrating, SOURCE, InstallationScope(), LIMIT)
    assert page.commit_cursor == records.REVISION
    assert [record.key.key for record in page.records] == ["key-0", "key-1"]


def test_replace_needs_the_captured_revision(main: SqliteDatabase) -> None:
    """A converted row leaves the stale page; a row at another revision fails the write."""
    records.seed(main, LABELS[:1])
    store = SqliteRecordMigrationStore(main)
    migrating = store.start(records.OWNER)
    stored = store.stale_page(migrating, SOURCE, InstallationScope(), LIMIT).records[0]
    converted = PutRecord(
        key=stored.key, expected_revision=stored.revision,
        document=migration_samples.document(NEW, '{"title":"First"}'), summary="Converted value.",
    )

    stale = converted.model_copy(update={"expected_revision": stored.revision + 1})
    with pytest.raises(ValueError, match="changed after it was captured"):
        store.replace(migrating, (stale,))
    store.replace(migrating, (converted,))

    assert not store.stale_page(migrating, SOURCE, InstallationScope(), LIMIT).records
    assert records.generation_rows(main, migrating.generation)[0].schema_version == NEW
    assert records.live_rows(main)[0].schema_version == OLD


def test_fail_and_a_new_manager_discard_the_copy(main: SqliteDatabase) -> None:
    """An explicit failure and a manager restart both remove a migrating copy; neither changes live rows."""
    records.seed(main, LABELS[:1])
    store = SqliteRecordMigrationStore(main)
    failed = store.start(records.OWNER)
    interrupted = store.start(records.OWNER)

    store.fail(failed)
    lifecycle = SqliteExtensionLifecycleRepository(main)
    assert lifecycle.claim_extension_manager(ManagerClaim(expected_revision=0, manager_id="next", claimed_at=1.0))

    assert records.generations(main) == {failed.generation: "failed", interrupted.generation: "failed"}
    assert not records.generation_rows(main, interrupted.generation)
    assert records.cursor(main, interrupted.generation) is None
    assert records.live_rows(main)[0].schema_version == OLD
