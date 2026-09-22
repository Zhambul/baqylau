# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid operation inputs before the external feature records execution."""

import asyncio
from pathlib import Path

import pytest
from baqylau_extension_api.models.commands import CommandRequest
from baqylau_extension_api.models.queries import QueryRequest
from baqylau_extension_api.runtime.commands import RemoteCommands
from baqylau_extension_api.runtime.models import ExtensionTransportError
from baqylau_extension_api.runtime.queries import RemoteQueries

from tests.extension_api import operation_process, operation_samples, samples


@pytest.mark.parametrize("change", [
    {"extension_id": "peer"}, {"runtime_revision": "stale"},
    {"operation_id": "test.sample.absent"}, {"scope": samples.SESSION},
])
def test_worker_rejects_command_binding(tmp_path: Path, change: dict[str, object]) -> None:
    """Keep a stale or undeclared attempt out of execute."""
    request = operation_samples.command_request()
    request = request.model_copy(update={"binding": request.binding.model_copy(update=change)})
    asyncio.run(_reject_command(tmp_path, request))


def test_worker_rejects_command_document(tmp_path: Path) -> None:
    """Validate encoded input, not only its JSON-RPC envelope."""
    asyncio.run(_reject_command(tmp_path, operation_samples.command_request("42")))


async def _reject_command(directory: Path, request: CommandRequest) -> None:
    async with operation_process.running_operations(directory) as caller:
        with pytest.raises(ExtensionTransportError):
            await asyncio.to_thread(RemoteCommands(caller).execute, request)
        assert await operation_process.read_counts(caller) == "executions:0;active:0;peers:1"


@pytest.mark.parametrize("change", [{"extension_id": "peer"}, {"runtime_revision": "stale"}])
def test_worker_rejects_query_binding(tmp_path: Path, change: dict[str, str]) -> None:
    """Reject a query from another package or runtime inside a valid channel."""
    request = operation_samples.query_request()
    request = request.model_copy(update={"binding": request.binding.model_copy(update=change)})
    asyncio.run(_reject_query(tmp_path, request))


def test_worker_rejects_query_document(tmp_path: Path) -> None:
    """Do not call a query with undeclared argument content."""
    asyncio.run(_reject_query(tmp_path, operation_samples.query_request("42")))


async def _reject_query(directory: Path, request: QueryRequest) -> None:
    async with operation_process.running_operations(directory) as caller:
        with pytest.raises(ExtensionTransportError):
            await asyncio.to_thread(RemoteQueries(caller).query, request)
        assert await operation_process.read_counts(caller) == "executions:0;active:0;peers:1"
