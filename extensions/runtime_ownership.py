# Copyright (c) 2026 Zhambyl Yermagambet
"""Use a native file lock without an expiring claim or a removable PID file."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock

from filelock import BaseFileLock, FileLock, Timeout

from extensions.runtime_ownership_contract import (
    ExtensionRuntimeLease,
    ExtensionRuntimeOwnership,
    RuntimeBusyError,
    RuntimeOwnershipError,
)

LOCK_NAME = "extension-runtime.lock"
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class NativeRuntimeLease(ExtensionRuntimeLease):
    """Own a held native lock; do not let a forked child use the parent's lease."""

    def __init__(self, native_lock: BaseFileLock) -> None:
        """Take ownership of a newly acquired, non-shared lock instance."""
        self._native_lock = native_lock
        self._process_id = os.getpid()
        self._lock = Lock()
        self._closed = False

    @contextmanager
    def hold_ownership(self) -> Iterator[None]:
        """Prevent release while the caller changes manager state.

        Yields:
            No public lock object or file descriptor.

        Raises:
            RuntimeOwnershipError: If the lease was closed or its native lock was lost.

        """
        self._require_process()
        with self._lock:
            if self._closed or not self._native_lock.is_locked:
                message = "extension runtime ownership is closed"
                raise RuntimeOwnershipError(message)
            yield

    def close(self) -> None:
        """Release once, keep the lock file, and permit cleanup from another thread."""
        self._require_process()
        with self._lock:
            if not self._closed:
                self._native_lock.release()
                self._closed = True

    def _require_process(self) -> None:
        if os.getpid() != self._process_id:
            message = "extension runtime ownership cannot be inherited by a child process"
            raise RuntimeOwnershipError(message)


class FilesystemRuntimeOwnership(ExtensionRuntimeOwnership):
    """Use one resolved data-directory path, independent of the dashboard port."""

    def __init__(self, directory: Path) -> None:
        """Resolve directory aliases before choosing the stable lock path."""
        self._directory = directory.resolve()

    def acquire_runtime(self) -> ExtensionRuntimeLease:
        """Attempt a native lock once; never fall back to a soft existence lock.

        Returns:
            A lease whose close method does not remove the lock file.

        Raises:
            RuntimeBusyError: If another lease holds this data directory.
            RuntimeOwnershipError: If the lock path is not a regular file.

        """
        self._directory.mkdir(mode=PRIVATE_DIRECTORY_MODE, parents=True, exist_ok=True)
        path = self._directory / LOCK_NAME
        if path.is_symlink() or (path.exists() and not path.is_file()):
            message = "extension runtime lock path must be a regular file"
            raise RuntimeOwnershipError(message)
        native_lock = FileLock(
            path, timeout=0, mode=PRIVATE_FILE_MODE, thread_local=False,
            fallback_to_soft=False, preserve_lock_file=True,
        )
        try:
            native_lock.acquire()
        except Timeout as exc:
            message = "extension runtime store already has an owner"
            raise RuntimeBusyError(message) from exc
        return NativeRuntimeLease(native_lock)
