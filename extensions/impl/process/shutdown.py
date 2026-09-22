# Copyright (c) 2026 Zhambyl Yermagambet
"""Stop only the owned process group and close its operating-system resources."""

import anyio
from anyio.abc import Process

from extensions.process_groups import kill_process_group


async def stop_process(process: Process, seconds: float) -> None:
    """Allow socket-close exit, then kill the group and release all process pipes."""
    with anyio.CancelScope(shield=True):
        with anyio.move_on_after(seconds):
            await process.wait()
        kill_process_group(process.pid)
        await process.aclose()
