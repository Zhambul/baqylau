# Copyright (c) 2026 Zhambyl Yermagambet
"""Read real external journal files through source worker protocols."""

import asyncio
import sys
from pathlib import Path

from baqylau_extension_api.models.source_results import SourceBatch
from baqylau_extension_api.runtime.sources import RemoteSources

from tests.extension_api import source_process, source_samples


def test_source_resumes_after_worker_restart(tmp_path: Path) -> None:
    """Resume from the same source position in a newly loaded worker."""
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(b'"first"\n"second"\n')
    asyncio.run(_resume(tmp_path, journal))
    assert "source_backend" not in sys.modules


async def _resume(directory: Path, journal: Path) -> None:
    async with source_process.running_source(directory, journal) as worker:
        request = await source_process.describe(worker)
        request = request.model_copy(update={"limit": 1})
        response = await asyncio.to_thread(RemoteSources(worker.caller).read, request)
        assert isinstance(response, SourceBatch) and response.has_more
        assert source_samples.first_document(response) == '"first"\n'
    async with source_process.running_source(directory, journal) as worker:
        request = request.model_copy(update={"after_position": response.next_position, "context": worker.context})
        response = await asyncio.to_thread(RemoteSources(worker.caller).read, request)
        assert isinstance(response, SourceBatch) and not response.has_more
        assert source_samples.first_document(response) == '"second"\n'


def test_partial_source_line_keeps_checkpoint(tmp_path: Path) -> None:
    """Do not consume a partial final line or request an immediate idle reread."""
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(b'"first"\n"second')
    asyncio.run(_partial(tmp_path, journal))


async def _partial(directory: Path, journal: Path) -> None:
    async with source_process.running_source(directory, journal) as worker:
        request = await source_process.describe(worker)
        response = await asyncio.to_thread(RemoteSources(worker.caller).read, request)
        assert isinstance(response, SourceBatch) and len(response.observations) == 1
        request = request.model_copy(update={"after_position": response.next_position})
        response = await asyncio.to_thread(RemoteSources(worker.caller).read, request)
        assert isinstance(response, SourceBatch) and not response.observations and not response.has_more
        assert response.next_position == request.after_position
        await asyncio.to_thread(journal.write_bytes, b'"first"\n"second"\n')
        response = await asyncio.to_thread(RemoteSources(worker.caller).read, request)
        assert isinstance(response, SourceBatch) and len(response.observations) == 1
        assert source_samples.first_document(response) == '"second"\n'


def test_replaced_source_requires_a_new_plan(tmp_path: Path) -> None:
    """Reject a read from a replaced file under its previous source generation."""
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(b'"old"\n')
    asyncio.run(_replace(tmp_path, journal))


async def _replace(directory: Path, journal: Path) -> None:
    async with source_process.running_source(directory, journal) as worker:
        request = await source_process.describe(worker)
        await asyncio.to_thread(_replace_journal, journal)
        response = await asyncio.to_thread(RemoteSources(worker.caller).read, request)
        assert response.status == "failed" and response.diagnostic.code == "source.changed"
        current = await source_process.describe(worker)
        assert current.source.source_identity != request.source.source_identity
        response = await asyncio.to_thread(RemoteSources(worker.caller).read, current)
        assert response.status == "ready"
        assert source_samples.first_document(response) == '"new"\n'


def _replace_journal(journal: Path) -> None:
    replacement = journal.with_name("replacement.jsonl")
    replacement.write_bytes(b'"new"\n')
    replacement.replace(journal)
