# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep source progress unchanged after bounded read failures and stale calls."""

import asyncio
from pathlib import Path

import pytest
from baqylau_extension_api.runtime.models import ExtensionTransportError
from baqylau_extension_api.runtime.sources import RemoteSources

from tests.extension_api import source_process

OVERSIZED_FIXTURE_LINE = 4097


@pytest.mark.parametrize(("content", "position", "code"), [
    (b"x" * OVERSIZED_FIXTURE_LINE, None, "source.line_limit"),
    (b'"one"\n', "999", "source.position_missing"),
])
def test_source_failure_has_no_new_checkpoint(
    tmp_path: Path, content: bytes, position: str | None, code: str,
) -> None:
    """Keep oversized or truncated source input out of successful read results."""
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(content)
    asyncio.run(_failed_read(tmp_path, journal, position, code))


async def _failed_read(directory: Path, journal: Path, position: str | None, code: str) -> None:
    async with source_process.running_source(directory, journal) as worker:
        request = await source_process.describe(worker)
        request = request.model_copy(update={"after_position": position})
        response = await asyncio.to_thread(RemoteSources(worker.caller).read, request)
        assert response.status == "failed" and response.diagnostic.code == code
        assert response.binding.after_position == position
        assert "next_position" not in response.model_dump()


@pytest.mark.parametrize("change", [{"runtime_revision": "stale"}, {"extension_id": "peer"}])
def test_source_rejects_stale_inner_context(tmp_path: Path, change: dict[str, str]) -> None:
    """Reject invalid scope authority inside a valid RPC envelope."""
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(b'"one"\n')
    asyncio.run(_stale_context(tmp_path, journal, change))


async def _stale_context(directory: Path, journal: Path, change: dict[str, str]) -> None:
    async with source_process.running_source(directory, journal) as worker:
        binding = worker.context.binding.model_copy(update=change)
        context = worker.context.model_copy(update={"binding": binding})
        with pytest.raises(ExtensionTransportError):
            await asyncio.to_thread(RemoteSources(worker.caller).describe, context)
        assert (await source_process.describe(worker)).source.source_identity
