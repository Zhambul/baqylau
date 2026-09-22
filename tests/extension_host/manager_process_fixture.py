# Copyright (c) 2026 Zhambyl Yermagambet
"""Use feature-owned process markers to check the actual daemon runtime path."""

from contextlib import closing
from pathlib import Path

from tests import terminal_pty_waits
from tests.extension_api import service_samples
from tests.extension_host import manager_fixture, package_fixture, runtime_host_fixture

BACKEND = "src/peer_backend.py"
ACTIVE = "active-worker"
STOPPED = "stopped-worker"
ENCODING = "utf-8"


def installed_peer(directory: Path, wheels: Path) -> int:
    """Capture and enable a real peer, then close the seed manager before the daemon.

    Returns:
        The stopped seed worker ID, which a daemon restart must replace.

    """
    marker_peer(directory, wheels)
    with closing(manager_fixture.open_manager(directory)) as host:
        host.finish()
        host.controller.submit_operation(host.proposal("seed"))
        host.finish()
        selected = worker_id(directory)
    terminal_pty_waits.wait_for_process_exit(selected)
    # The seed fixture uses a short root name; the daemon uses this fixed name.
    (directory / "artifacts").rename(directory / "extension-artifacts")
    return selected


def marker_peer(directory: Path, wheels: Path) -> Path:
    """Create a real backend with feature-owned activation and shutdown markers.

    Returns:
        The external source package before capture or activation.

    """
    source = runtime_host_fixture.write_peers(directory, wheels, (service_samples.ALPHA,))[0]
    _add_markers(source, directory)
    return source


def worker_id(directory: Path) -> int:
    """Read a complete feature marker after activation.

    Returns:
        The real worker process ID, not a stored lifecycle-state claim.

    """
    return int((directory / ACTIVE).read_text(encoding=ENCODING))


def replacement_worker(directory: Path, previous: int) -> int:
    """Wait for the actual activated worker to replace the seed generation.

    Returns:
        A complete, distinct process marker from the external backend.

    """
    terminal_pty_waits.wait_until(lambda: _replaced(directory, previous))
    return worker_id(directory)


def require_stopped(directory: Path, selected: int) -> None:
    """Require both the package's deactivation reply path and actual process exit."""
    assert (directory / STOPPED).read_text(encoding=ENCODING) == str(selected)
    terminal_pty_waits.wait_for_process_exit(selected)
    assert not tuple((directory / "extension-environments").iterdir())


def _replaced(directory: Path, previous: int) -> bool:
    selected = (directory / ACTIVE).read_text(encoding=ENCODING)
    return bool(selected) and int(selected) != previous


def _add_markers(source: Path, directory: Path) -> None:
    code = (source / BACKEND).read_text(encoding=ENCODING)
    code = code.replace(
        "from dataclasses import dataclass", "from dataclasses import dataclass\nimport os\nfrom pathlib import Path",
    )
    code = code.replace(
        "return lifecycle_models.ActivationReady",
        f"Path({str(directory / ACTIVE)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        "        return lifecycle_models.ActivationReady",
    )
    code = code.replace(
        "return lifecycle_models.DeactivationResult",
        f"Path({str(directory / STOPPED)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        "        return lifecycle_models.DeactivationResult",
    )
    package_fixture.write_file(source, BACKEND, code.encode())
