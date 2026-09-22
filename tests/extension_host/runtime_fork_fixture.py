# Copyright (c) 2026 Zhambyl Yermagambet
"""Check fork inheritance in a single-threaded child, not in the pytest process."""

import os
import signal
from pathlib import Path

import pytest

from extensions.runtime_ownership import FilesystemRuntimeOwnership
from extensions.runtime_ownership_contract import ExtensionRuntimeLease, RuntimeBusyError, RuntimeOwnershipError


def check_inherited_lease(lease: ExtensionRuntimeLease, directory: Path) -> None:
    """Fork while the parent holds its lease mutex and require immediate rejection."""
    process_id = os.fork()
    if process_id == 0:
        signal.alarm(5)
        _require_child_rejection(lease)
        os._exit(0)
    finished, status = os.waitpid(process_id, 0)
    assert finished == process_id and os.waitstatus_to_exitcode(status) == 0
    with pytest.raises(RuntimeBusyError):
        FilesystemRuntimeOwnership(directory).acquire_runtime()


def _require_child_rejection(lease: ExtensionRuntimeLease) -> None:
    with pytest.raises(RuntimeOwnershipError, match="inherited"), lease.hold_ownership():
        pytest.fail("the child must not enter its parent's ownership context")
    with pytest.raises(RuntimeOwnershipError, match="inherited"):
        lease.close()
