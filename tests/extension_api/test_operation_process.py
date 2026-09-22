# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify separate-process queries, command cancellation, and reconciliation."""

import asyncio
import sys
from pathlib import Path

from baqylau_extension_api.models.commands import CommandCancelRequest, CommandReconcileRequest
from baqylau_extension_api.models.transforms import Keep
from baqylau_extension_api.runtime.bridge import RpcBridge
from baqylau_extension_api.runtime.commands import RemoteCommands
from baqylau_extension_api.runtime.proxies import RemoteRawTransformer

from tests.extension_api import operation_process, operation_samples, worker_samples


def test_operations_cross_the_process_boundary(tmp_path: Path) -> None:
    """Load queries and commands without importing feature code in the host."""
    asyncio.run(_round_trip(tmp_path))
    assert "operations_backend" not in sys.modules


async def _round_trip(directory: Path) -> None:
    async with operation_process.running_operations(directory) as caller:
        assert await operation_process.read_counts(caller) == "executions:0;active:0;peers:1"
        request = operation_samples.command_request()
        result = await asyncio.to_thread(RemoteCommands(caller).execute, request)
        assert result.status == "succeeded"
        assert result.document == request.arguments
        assert await operation_process.read_counts(caller) == "executions:1;active:0;peers:1"


def test_slow_command_allows_reads_and_stop(tmp_path: Path) -> None:
    """Run a query, host callback, transform, and cancellation during execution."""
    asyncio.run(_slow_command(tmp_path))


async def _slow_command(directory: Path) -> None:
    async with operation_process.running_operations(directory) as caller:
        request = operation_samples.command_request('"wait"')
        async with asyncio.TaskGroup() as group:
            pending = group.create_task(asyncio.to_thread(RemoteCommands(caller).execute, request))
            await operation_process.wait_for_active(caller)
            transformed = await asyncio.to_thread(RemoteRawTransformer(caller).transform, worker_samples.raw_request())
            assert transformed.operations == (Keep(input_id="raw-1"),)
            assert not pending.done()
            await _cancel_exact_attempt(caller)
        assert pending.result().status == "canceled"
        assert await operation_process.read_counts(caller) == "executions:1;active:0;peers:1"


async def _cancel_exact_attempt(caller: RpcBridge) -> None:
    commands = RemoteCommands(caller)
    request = CommandCancelRequest(binding=operation_samples.command_request().binding, reason="Stop fixture work.")
    binding = request.binding.model_copy(update={"call_id": "earlier-attempt"})
    stale = request.model_copy(update={"binding": binding})
    response = await asyncio.to_thread(commands.cancel, stale)
    assert response.status == "not_running"
    assert await operation_process.read_counts(caller) == "executions:1;active:1;peers:1"
    response = await asyncio.to_thread(commands.cancel, request)
    assert response.status == "requested"


def test_reconciliation_does_not_repeat_execution(tmp_path: Path) -> None:
    """Check stored fixture proof through reconcile, with no second execute call."""
    asyncio.run(_reconcile(tmp_path))


async def _reconcile(directory: Path) -> None:
    async with operation_process.running_operations(directory) as caller:
        request = operation_samples.command_request('"unknown"')
        uncertain = await asyncio.to_thread(RemoteCommands(caller).execute, request)
        assert uncertain.status == "outcome_unknown"
        recovery = CommandReconcileRequest(command=request.model_copy(update={
            "binding": request.binding.model_copy(update={"call_id": "reconcile-1"}),
        }), receipt=uncertain.receipt)
        result = await asyncio.to_thread(RemoteCommands(caller).reconcile, recovery)
        assert result.status == "succeeded"
        assert result.binding == recovery.command.binding
        assert await operation_process.read_counts(caller) == "executions:1;active:0;peers:1"
