# Copyright (c) 2026 Zhambyl Yermagambet
"""Translate captured external source bytes without reopening the journal."""

import asyncio
from pathlib import Path

import pytest
from baqylau_extension_api.models.sources import SourceReleaseRequest
from baqylau_extension_api.runtime.models import ExtensionTransportError
from baqylau_extension_api.runtime.sources import RemoteSources
from baqylau_extension_api.runtime.translation import RemoteTranslator
from baqylau_extension_api.translation.results import translated_candidates

from tests.extension_api import source_process


def test_translation_replays_after_source_is_gone(tmp_path: Path) -> None:
    """Keep fact IDs and documents when the history revision and processing mode change."""
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(b'"first"\n"first"\n')
    asyncio.run(_replay(tmp_path, journal))


async def _replay(directory: Path, journal: Path) -> None:
    async with source_process.running_source(directory, journal) as worker:
        batch = await source_process.read_all(worker)
        captured = source_process.capture_input(worker.context, batch)
        await asyncio.to_thread(journal.unlink)
        response = await asyncio.to_thread(RemoteTranslator(worker.caller).translate, captured)
        assert len(response.decisions) == len(batch.observations)
        assert len(translated_candidates(response)) == 1
        captured = captured.model_copy(update={
            "context": captured.context.model_copy(update={"history_revision": "history-2", "mode": "replay"}),
        })
        replayed = await asyncio.to_thread(RemoteTranslator(worker.caller).translate, captured)
        assert translated_candidates(response) == translated_candidates(replayed)
        assert replayed.context.scope.kind == "repository"
        await _release_scope(worker)


async def _release_scope(worker: source_process.SourceProcess) -> None:
    request = SourceReleaseRequest(binding=worker.context.binding, reason="Stop the fixture scope.")
    response = await asyncio.to_thread(RemoteSources(worker.caller).release, request)
    assert response.status == "released" and response.source_identity is None


def test_translation_cannot_use_live_services(tmp_path: Path) -> None:
    """Reject a host callback from the pure translation lane."""
    journal = tmp_path / "journal.jsonl"
    journal.write_bytes(b'"host_call"\n')
    asyncio.run(_forbidden_service(tmp_path, journal))


async def _forbidden_service(directory: Path, journal: Path) -> None:
    async with source_process.running_source(directory, journal) as worker:
        batch = await source_process.read_all(worker)
        captured = source_process.capture_input(worker.context, batch)
        with pytest.raises(ExtensionTransportError):
            await asyncio.to_thread(RemoteTranslator(worker.caller).translate, captured)
