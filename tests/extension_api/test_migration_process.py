# Copyright (c) 2026 Zhambyl Yermagambet
"""Check isolated migration, failure, retry, reverse paths, and pure-call guards."""

import asyncio
import sys
from pathlib import Path

import pytest
from baqylau_extension_api.models import migration_results as outcomes
from baqylau_extension_api.models.migrations import SettingsMigrationRequest
from baqylau_extension_api.runtime.models import ExtensionTransportError

from tests.extension_api import migration_process, migration_samples as fixtures


def test_external_migrations_repeat_after_restart(tmp_path: Path) -> None:
    """Both pure conversions produce the same candidates after a worker restart."""
    first = asyncio.run(_round_trip(tmp_path))
    assert first == asyncio.run(_round_trip(tmp_path))
    assert first[0].document == fixtures.document(2, '{"title":"Before"}')
    revisions = tuple(record.expected_revision for record in first[1].records)
    assert revisions == (6, 6)
    assert "migration_backend" not in sys.modules


async def _round_trip(directory: Path) -> tuple[outcomes.SettingsMigrationReady, outcomes.RecordMigrationReady]:
    async with migration_process.running_migrations(directory) as proxy:
        settings = await asyncio.to_thread(proxy.migrate_settings, fixtures.settings_request())
        records = await asyncio.to_thread(proxy.migrate_records, fixtures.records_request())
        assert isinstance(settings, outcomes.SettingsMigrationReady)
        assert isinstance(records, outcomes.RecordMigrationReady)
        return settings, records


@pytest.mark.parametrize("mode", ["host_call", "stale"])
def test_rejected_call_keeps_worker_usable(tmp_path: Path, mode: str) -> None:
    """A blocked live callback or stale revision cannot replace candidate data."""
    asyncio.run(_rejected_call(tmp_path, mode))


async def _rejected_call(directory: Path, mode: str) -> None:
    request = fixtures.settings_request()
    if mode == "stale":
        binding = request.binding.model_copy(update={"runtime_revision": "stale"})
        request = request.model_copy(update={"binding": binding})
    else:
        request = request.model_copy(update={"source": fixtures.document(1, '{"label":"host_call"}')})
    async with migration_process.running_migrations(directory) as proxy:
        with pytest.raises(ExtensionTransportError):
            await asyncio.to_thread(proxy.migrate_settings, request)
        ready = await asyncio.to_thread(proxy.migrate_settings, fixtures.settings_request())
        assert isinstance(ready, outcomes.SettingsMigrationReady)


def test_declared_downgrade_in_external_worker(tmp_path: Path) -> None:
    """A separately declared reverse path converts values without a code reload in the host."""
    request = fixtures.settings_request().model_copy(update={
        "source_schema": fixtures.schema(2).reference, "target_schema": fixtures.schema(1).reference,
        "source": fixtures.document(2, '{"title":"Before"}'),
    })
    response = asyncio.run(_downgrade(tmp_path, request))
    assert isinstance(response, outcomes.SettingsMigrationReady)
    assert response.document == fixtures.document(1, '{"label":"Before"}')


async def _downgrade(directory: Path, request: SettingsMigrationRequest) -> outcomes.SettingsMigrationResult:
    async with migration_process.running_migrations(directory, downgrade=True) as proxy:
        return await asyncio.to_thread(proxy.migrate_settings, request)
