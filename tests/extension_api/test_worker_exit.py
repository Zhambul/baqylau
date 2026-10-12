# Copyright (c) 2026 Zhambyl Yermagambet
"""A worker ends with its host connection, also while a call still runs.

A call that hung kept its lane thread alive, and Python waits for that thread
before a process can end. So the worker stayed after its host was gone: a test
daemon that ended under load left one worker for an hour.
"""

import subprocess  # noqa: S404 -- The test runs the worker entry point in its own process.
import sys

EXIT_SECONDS = 20
HUNG_CALL = """
import asyncio, sys, time
from baqylau_extension_api.runtime import execution, worker

class Hang:
    def invoke(self, encoded):
        time.sleep(3600)
        return encoded

async def served(arguments):
    lane = execution.ExecutionLane(1, pure=False)
    asyncio.ensure_future(lane.run(Hang(), "{}"))
    await asyncio.sleep(0.2)
    lane.close()

worker._serve = served
sys.argv = ["worker", "--rpc-fd", "0", "--runtime-revision", "revision", "--package-directory", "."]
worker.main()
"""


def test_worker_ends_with_its_connection() -> None:
    """The host connection ends while a call hangs in its lane, and the worker process ends at once."""
    finished = subprocess.run(  # noqa: S603 -- The command is this interpreter and a fixed script.
        (sys.executable, "-c", HUNG_CALL), timeout=EXIT_SECONDS, check=False,
    )

    assert finished.returncode == 0
