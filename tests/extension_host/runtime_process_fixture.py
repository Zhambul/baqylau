# Copyright (c) 2026 Zhambyl Yermagambet
"""Use short private child processes for lock and interrupted-commit checks."""

import os
import select
import subprocess  # noqa: S404 -- Run fixed private test programs without a shell.
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from extensions import registry_contract, runtime_commit
from extensions.models.lifecycle_selection import RuntimeSelection
from extensions.runtime_ownership import FilesystemRuntimeOwnership
from tests.extension_host import runtime_commit_fixture as commits, runtime_fork_fixture

EXIT_CODE = 91
MODULE = "tests.extension_host.runtime_process_fixture"


@dataclass(frozen=True)
class ExitCommit(registry_contract.RegistryCommit):
    """Stop before or after the durable commit, while the registry lock is held."""

    commit: runtime_commit.StoredRegistryCommit
    stage: str

    def commit_registry(self, selection: RuntimeSelection) -> bool:
        """Exit without normal cleanup at the selected publication boundary."""
        if self.stage == "after":
            assert self.commit.commit_registry(selection)
        os._exit(EXIT_CODE)


@contextmanager
def lock_process(directory: Path) -> Iterator[subprocess.Popen[bytes]]:
    """Start one lock owner and stop only this test-owned process on exit.

    Yields:
        A child which holds the lock until stdin closes or the process is killed.

    """
    with subprocess.Popen(  # noqa: S603 -- Use this interpreter and a fixed test module.
        (sys.executable, "-m", MODULE, "lock", str(directory)),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={"PATH": os.defpath},
    ) as process:
        try:
            yield _ready_process(process)
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def interrupt_commit(directory: Path, stage: str) -> None:
    """Run a private registry publication which cannot return normally."""
    completed = subprocess.run(  # noqa: S603 -- Use this interpreter and a fixed test module.
        (sys.executable, "-m", MODULE, stage, str(directory)),
        capture_output=True, check=False, timeout=10, env={"PATH": os.defpath},
    )
    assert completed.returncode == EXIT_CODE, completed.stderr.decode()


def _ready_process(process: subprocess.Popen[bytes]) -> subprocess.Popen[bytes]:
    assert process.stdout is not None
    assert select.select((process.stdout,), (), (), 10)[0], "lock child did not start"
    assert process.stdout.readline() == b"ready\n"
    return process


def _main(stage: str, directory: Path) -> None:
    lease = FilesystemRuntimeOwnership(directory).acquire_runtime()
    with lease.hold_ownership():
        if stage == "lock":
            sys.stdout.write("ready\n")
            sys.stdout.flush()
            sys.stdin.buffer.read(1)
        elif stage == "fork":
            runtime_fork_fixture.check_inherited_lease(lease, directory)
            os._exit(EXIT_CODE)
        else:
            case = commits.accepted(directory)
            case.registry.publish_snapshot(0, case.snapshot, ExitCommit(case.commit, stage))
    lease.close()


if __name__ == "__main__":
    _main(sys.argv[1], Path(sys.argv[2]))
