# Copyright (c) 2026 Zhambyl Yermagambet
"""Check exclusive native locks, explicit close, and data-directory identity."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from stat import S_IMODE

import pytest

from extensions.runtime_ownership import LOCK_NAME, PRIVATE_FILE_MODE, FilesystemRuntimeOwnership
from extensions.runtime_ownership_contract import RuntimeBusyError, RuntimeOwnershipError


def test_same_factory_does_not_reenter_lock(tmp_path: Path) -> None:
    """Two acquisitions cannot share one library instance and both become owners."""
    ownership = FilesystemRuntimeOwnership(tmp_path)
    with (
        closing(ownership.acquire_runtime()) as lease, lease.hold_ownership(),
        pytest.raises(RuntimeBusyError, match="already has an owner"),
    ):
        ownership.acquire_runtime()
    with closing(ownership.acquire_runtime()) as replacement, replacement.hold_ownership():
        assert (tmp_path / LOCK_NAME).is_file()


def test_directory_alias_uses_same_lock(tmp_path: Path) -> None:
    """A directory symlink cannot create a second owner for the same store."""
    directory = tmp_path / "data"
    directory.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    with closing(FilesystemRuntimeOwnership(directory).acquire_runtime()), pytest.raises(RuntimeBusyError):
        FilesystemRuntimeOwnership(alias).acquire_runtime()
    assert (alias / LOCK_NAME).stat().st_ino == (directory / LOCK_NAME).stat().st_ino


def test_close_is_final_and_keeps_inode(tmp_path: Path) -> None:
    """An old lease cannot release or authorize a later owner."""
    lease = FilesystemRuntimeOwnership(tmp_path).acquire_runtime()
    inode = (tmp_path / LOCK_NAME).stat().st_ino
    lease.close()
    with closing(FilesystemRuntimeOwnership(tmp_path).acquire_runtime()) as replacement:
        lease.close()
        with pytest.raises(RuntimeOwnershipError, match="closed"), lease.hold_ownership():
            pytest.fail("a closed lease cannot enter an ownership context")
        with replacement.hold_ownership():
            assert (tmp_path / LOCK_NAME).stat().st_ino == inode
        with pytest.raises(RuntimeBusyError):
            FilesystemRuntimeOwnership(tmp_path).acquire_runtime()


def test_lease_can_close_from_another_thread(tmp_path: Path) -> None:
    """Cleanup need not run on the thread that started this manager."""
    lease = FilesystemRuntimeOwnership(tmp_path).acquire_runtime()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lease.close).result(timeout=3)
    with (
        closing(FilesystemRuntimeOwnership(tmp_path).acquire_runtime()) as replacement,
        replacement.hold_ownership(),
    ):
        assert S_IMODE((tmp_path / LOCK_NAME).stat().st_mode) == PRIVATE_FILE_MODE


@pytest.mark.parametrize("kind", ["directory", "symlink", "broken_symlink"])
def test_nonregular_lock_path_is_rejected(tmp_path: Path, kind: str) -> None:
    """The owner never follows a lock-file symlink or removes an unexpected path."""
    path = tmp_path / LOCK_NAME
    if kind == "directory":
        path.mkdir()
    else:
        path.symlink_to(tmp_path if kind == "symlink" else tmp_path / "missing")
    with pytest.raises(RuntimeOwnershipError, match="regular file"):
        FilesystemRuntimeOwnership(tmp_path).acquire_runtime()
    assert path.is_dir() or path.is_symlink()
