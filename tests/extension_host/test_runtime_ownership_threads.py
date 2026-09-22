# Copyright (c) 2026 Zhambyl Yermagambet
"""Check release ordering and fail-closed behavior when native locks are absent."""

import errno
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from threading import Event

import pytest

from extensions.runtime_ownership import LOCK_NAME, FilesystemRuntimeOwnership
from extensions.runtime_ownership_contract import ExtensionRuntimeLease, RuntimeBusyError


def test_close_waits_for_current_owner(tmp_path: Path) -> None:
    """A cleanup thread cannot release ownership halfway through a state change."""
    lease = FilesystemRuntimeOwnership(tmp_path).acquire_runtime()
    started = Event()
    with closing(lease), ThreadPoolExecutor(max_workers=1) as pool:
        with lease.hold_ownership():
            pending = pool.submit(_close_started, lease, started)
            assert started.wait(3)
            assert not pending.done()
            with pytest.raises(RuntimeBusyError):
                FilesystemRuntimeOwnership(tmp_path).acquire_runtime()
        pending.result(timeout=3)
    with closing(FilesystemRuntimeOwnership(tmp_path).acquire_runtime()):
        assert (tmp_path / LOCK_NAME).is_file()


def test_distinct_stores_have_separate_owners(tmp_path: Path) -> None:
    """A private test or a second data directory does not contend with this runtime."""
    with (
        closing(FilesystemRuntimeOwnership(tmp_path / "first").acquire_runtime()) as first,
        closing(FilesystemRuntimeOwnership(tmp_path / "second").acquire_runtime()) as second,
        first.hold_ownership(), second.hold_ownership(),
    ):
        assert (tmp_path / "first" / LOCK_NAME).is_file()
        assert (tmp_path / "second" / LOCK_NAME).is_file()


def test_missing_native_lock_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unsupported filesystem cannot cause a switch to removable existence locks."""
    native = pytest.importorskip("fcntl")
    monkeypatch.setattr(native, "flock", _unsupported_flock)
    with pytest.raises(OSError, match="unsupported native lock"):
        FilesystemRuntimeOwnership(tmp_path).acquire_runtime()
    assert (tmp_path / LOCK_NAME).is_file()


def _close_started(lease: ExtensionRuntimeLease, started: Event) -> None:
    started.set()
    lease.close()


def _unsupported_flock(descriptor: int, operation: int) -> None:
    assert descriptor >= 0 and operation > 0
    message = "unsupported native lock"
    raise OSError(errno.ENOSYS, message)
