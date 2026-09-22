# Copyright (c) 2026 Zhambyl Yermagambet
"""Supply observable fault modes after a real worker has reported ready."""

from pathlib import Path
from time import monotonic, sleep

from baqylau_extension_api.models.lifecycle import ActivationRequest

from extensions.worker_contract import ExtensionWorker
from tests.extension_api import samples
from tests.extension_host import package_fixture, worker_fixture

FAULT_SOURCE = Path(__file__).with_name("fixtures") / "worker_faults.py.txt"
WAIT_SECONDS = 3
POLL_SECONDS = 0.01


def write_package(directory: Path, wheels: Path, mode: str) -> Path:
    """Extend the typed fixture lifecycle with one controlled runtime fault.

    Returns:
        The mutable package before its final capture.

    """
    source = worker_fixture.write_package(directory, wheels)
    original = (source / worker_fixture.SOURCE_PATH).read_text(encoding="utf-8")
    suffix = FAULT_SOURCE.read_text(encoding="utf-8")
    package_fixture.write_file(
        source, worker_fixture.SOURCE_PATH, f"{original}\nFAULT_MODE = {mode!r}\n{suffix}".encode(),
    )
    return source


def activation() -> ActivationRequest:
    """Select the prepared fixture runtime without changing any application state.

    Returns:
        A normal typed lifecycle request.

    """
    return ActivationRequest(runtime_revision=samples.RUNTIME_REVISION, settings_revision=0)


def wait_for_activation(worker: ExtensionWorker) -> None:
    """Wait for actual worker output, not an assumed scheduling delay.

    Raises:
        AssertionError: If the process does not start the requested activation.

    """
    deadline = monotonic() + WAIT_SECONDS
    while b"activation-started" not in worker.diagnostics().stdout:
        if monotonic() >= deadline:
            message = "fixture activation did not start"
            raise AssertionError(message)
        sleep(POLL_SECONDS)


def wait_for_exit(worker: ExtensionWorker) -> None:
    """Observe the owned process becoming terminal through retained diagnostics.

    Raises:
        AssertionError: If the failed process remains live beyond the test deadline.

    """
    deadline = monotonic() + WAIT_SECONDS
    while worker.diagnostics().return_code is None:
        if monotonic() >= deadline:
            message = "failed worker did not exit"
            raise AssertionError(message)
        sleep(POLL_SECONDS)
