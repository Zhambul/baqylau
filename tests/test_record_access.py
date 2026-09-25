# Copyright (c) 2026 Zhambyl Yermagambet
"""Read a worker's own declared record collection; refuse an undeclared one."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.record_reads import RecordPageRequest
from baqylau_extension_api.models.scopes import InstallationScope

from extensions.record_access import HostRecordReader
from repository.impl.sqlite.extension_records import SqliteExtensionRecordRepository
from tests.extension_api import migration_samples
from tests.extension_host import record_migration_fixture as records

if TYPE_CHECKING:
    from repository.impl.sqlite.connection import SqliteDatabase


def test_reads_own_collection_in_pages(main: SqliteDatabase) -> None:
    """The first page names the key that starts the next page."""
    records.seed(main, ("First", "Second"))
    reader = HostRecordReader(migration_samples.manifest(), SqliteExtensionRecordRepository(main))

    request = RecordPageRequest(collection=records.COLLECTION, scope=InstallationScope(), limit=1)
    page = reader.read_records(request)

    assert [state.key.key for state in page.records] == ["key-0"]
    assert page.next_key == "key-0"


def test_undeclared_collection_is_refused(main: SqliteDatabase) -> None:
    """A worker cannot read a collection that its manifest does not declare."""
    reader = HostRecordReader(migration_samples.manifest(), SqliteExtensionRecordRepository(main))

    with pytest.raises(ExtensionContractError, match="does not declare"):
        reader.read_records(RecordPageRequest(collection="other.records", scope=InstallationScope()))
