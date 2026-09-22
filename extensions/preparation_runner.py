# Copyright (c) 2026 Zhambyl Yermagambet
"""Run finite preparation work without collecting unbounded subprocess output."""

import math

import anyio
from anyio.abc import Process

from extensions.environment_contract import PreparationRunner
from extensions.models.processes import PreparationCommand, PreparationOutput
from extensions.preparation_output import PreparationBuffers, PreparationOutputLimitError
from extensions.preparation_process import owned_preparation


class BoundedPreparationRunner(PreparationRunner):
    """Provide a synchronous boundary backed by two asynchronous stream readers."""

    def run_preparation(self, preparation_command: PreparationCommand) -> PreparationOutput:
        """Run a command in its own group and enforce its deadline and byte budget.

        Returns:
            Bounded output after the process group has been released.

        """
        _require_budget(preparation_command)
        return anyio.run(_run_command, preparation_command)


def _require_budget(preparation_command: PreparationCommand) -> None:
    finite = math.isfinite(preparation_command.timeout_seconds)
    if not finite or preparation_command.timeout_seconds <= 0 or preparation_command.output_limit <= 0:
        message = "preparation requires a positive finite deadline and output limit"
        raise ValueError(message)
    if not preparation_command.arguments:
        message = "preparation requires an explicit command"
        raise ValueError(message)


async def _run_command(preparation_command: PreparationCommand) -> PreparationOutput:
    async with owned_preparation(preparation_command) as process:
        with anyio.fail_after(preparation_command.timeout_seconds):
            return await _collect_output(process, preparation_command.output_limit)


async def _collect_output(process: Process, limit: int) -> PreparationOutput:
    buffers = PreparationBuffers(limit)
    try:
        await _drain_output(process, buffers)
    except* PreparationOutputLimitError:
        message = "extension preparation exceeded its output limit"
        raise PreparationOutputLimitError(message) from None
    return_code = await process.wait()
    return PreparationOutput(bytes(buffers.stdout), bytes(buffers.stderr), return_code)


async def _drain_output(process: Process, buffers: PreparationBuffers) -> None:
    async with anyio.create_task_group() as group:
        if process.stdout is not None:
            group.start_soon(buffers.drain, process.stdout, buffers.stdout)
        if process.stderr is not None:
            group.start_soon(buffers.drain, process.stderr, buffers.stderr)
