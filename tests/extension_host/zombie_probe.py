# Copyright (c) 2026 Zhambyl Yermagambet
"""Check real group cleanup with a zombie in a separate single-threaded test process."""

import os
from contextlib import ExitStack
from time import monotonic, sleep

import psutil

from extensions.process_groups import kill_process_group

WAIT_SECONDS = 3
POLL_SECONDS = 0.01


def main() -> None:
    """Keep the exited child unreaped until its process group cleanup is checked."""
    child = os.fork()
    if child == 0:
        os.setsid()
        os._exit(0)
    with ExitStack() as cleanup:
        cleanup.callback(os.waitpid, child, 0)
        _wait_for_zombie(child)
        kill_process_group(child)


def _wait_for_zombie(child: int) -> None:
    deadline = monotonic() + WAIT_SECONDS
    while psutil.Process(child).status() != psutil.STATUS_ZOMBIE:
        if monotonic() >= deadline:
            message = "the fixture child did not exit"
            raise AssertionError(message)
        sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
