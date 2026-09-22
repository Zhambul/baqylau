# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid migration output inside the worker, before any batch is returned."""

import asyncio
from pathlib import Path

import pytest
from baqylau_extension_api.models.migration_results import RecordMigrationFailed, RecordMigrationReady
from baqylau_extension_api.models.migrations import RecordMigrationRequest
from baqylau_extension_api.runtime.models import ExtensionTransportError

from tests.extension_api import migration_process, migration_samples as fixtures


@pytest.mark.parametrize("mode", ["host_call", "invalid"])
def test_worker_rejects_bad_record_conversion(tmp_path: Path, mode: str) -> None:
    """A bad body or live call rejects the whole reply and leaves the worker usable."""
    asyncio.run(_rejected_batch(tmp_path, mode))


async def _rejected_batch(directory: Path, mode: str) -> None:
    request = _record_mode(mode)
    async with migration_process.running_migrations(directory) as proxy:
        with pytest.raises(ExtensionTransportError):
            await asyncio.to_thread(proxy.migrate_records, request)
        response = await asyncio.to_thread(proxy.migrate_records, fixtures.records_request())
        assert isinstance(response, RecordMigrationReady)
        assert len(response.records) == len(request.records)


def test_worker_returns_record_failure(tmp_path: Path) -> None:
    """A supported call can return a known failure with no partial candidate rows."""
    asyncio.run(_failed_batch(tmp_path))


async def _failed_batch(directory: Path) -> None:
    async with migration_process.running_migrations(directory) as proxy:
        response = await asyncio.to_thread(proxy.migrate_records, _record_mode("reject"))
        assert isinstance(response, RecordMigrationFailed)
        assert response.diagnostic.code == "unsupported_value"
        assert "records" not in response.model_fields_set


def _record_mode(mode: str) -> RecordMigrationRequest:
    request = fixtures.records_request()
    document = fixtures.document(1, f'{{"label":"{mode}"}}')
    first = request.records[0].model_copy(update={"document": document})
    return request.model_copy(update={"records": (first, request.records[1])})
