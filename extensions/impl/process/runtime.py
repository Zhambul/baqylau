# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep one worker's process, channel, monitors, and typed proxies in one lifetime."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.runtime import bridge, channel as rpc_channel, methods, models, worker_models
from pydantic import TypeAdapter

from extensions.environment_contract import WorkerEnvironment
from extensions.impl.process import capabilities, channel, launch, monitor, output, plugin, selection
from extensions.models.workers import WorkerDiagnostics, WorkerPolicy
from extensions.worker_errors import WorkerStartError


@dataclass(frozen=True)
class RunningWorker:
    """Retain public proxies and bounded evidence after process cleanup."""

    process: launch.WorkerProcess
    plugin: plugin.ProcessExtensionPlugin
    output: output.WorkerOutput

    def diagnostics(self) -> WorkerDiagnostics:
        """Read captured output and the process's current exit code.

        Returns:
            Bounded evidence from this exact process.

        """
        return self.output.snapshot(self.process.process.returncode)


@asynccontextmanager
async def running_worker(
    environment: WorkerEnvironment, request: worker_models.WorkerLoadRequest,
    services: ExtensionHostServices, policy: WorkerPolicy, revoke: Callable[[], None],
) -> AsyncIterator[RunningWorker]:
    """Load a checked feature while its transport and stream readers remain active.

    Yields:
        The complete process-backed plugin after its ready reply has been checked.

    Raises:
        WorkerStartError: If loading fails after the process has started.

    """
    async with AsyncExitStack() as cleanup:
        process = await cleanup.enter_async_context(launch.launched_worker(
            environment, request.environment.runtime_revision, policy.stop_seconds,
        ))
        rpc = await cleanup.enter_async_context(channel.worker_channel(
            process.endpoint, services, policy.request_seconds,
        ))
        logs = output.WorkerOutput(process.process.pid, policy.output_limit)
        monitor.WorkerMonitor(process.process, rpc, logs, revoke).start(cleanup)
        try:
            prepared = await _load_plugin(rpc, request, policy)
        except (models.ExtensionTransportError, ValueError):
            logs.load_failed()
            await cleanup.aclose()
            raise WorkerStartError(logs.snapshot(process.process.returncode)) from None
        yield RunningWorker(process, prepared, logs)


async def _load_plugin(
    rpc: rpc_channel.RpcChannel, request: worker_models.WorkerLoadRequest, policy: WorkerPolicy,
) -> plugin.ProcessExtensionPlugin:
    ready = await rpc.call(methods.LOAD, request, TypeAdapter(worker_models.WorkerReady))
    _check_ready(request, ready)
    caller = bridge.RpcBridge(rpc, asyncio.get_running_loop(), policy.request_seconds)
    selected = capabilities.process_capabilities(selection.ProxySelection(ready.capabilities, caller))
    return plugin.ProcessExtensionPlugin(ready.extension_info, selected)


def _check_ready(request: worker_models.WorkerLoadRequest, ready: worker_models.WorkerReady) -> None:
    if ready.extension_info != request.environment.extension_info:
        message = "worker ready identity does not match the selected package"
        raise ValueError(message)
    if set(ready.capabilities) != set(request.manifest.capabilities):
        message = "worker ready capabilities do not match the selected package"
        raise ValueError(message)
