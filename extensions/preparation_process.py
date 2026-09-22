# Copyright (c) 2026 Zhambyl Yermagambet
"""Own a separate process group for each bounded preparation command."""

from asyncio.subprocess import DEVNULL, PIPE
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from anyio.abc import Process

from extensions.models.processes import PreparationCommand
from extensions.process_groups import kill_process_group


@asynccontextmanager
async def owned_preparation(preparation_command: PreparationCommand) -> AsyncIterator[Process]:
    """Release all still-owned group members on success, failure, or cancellation.

    Yields:
        The process with separate output streams and no input stream.

    """
    process = await anyio.open_process(
        preparation_command.arguments, stdin=DEVNULL, stdout=PIPE, stderr=PIPE,
        cwd=preparation_command.directory, env=preparation_command.environment, start_new_session=True,
    )
    try:
        yield process
    finally:
        with anyio.CancelScope(shield=True):
            kill_process_group(process.pid)
            await process.aclose()
