# Copyright (c) 2026 Zhambyl Yermagambet
"""Require one process owner before a manager changes extension runtime state."""

from contextlib import AbstractContextManager
from typing import Protocol


class RuntimeOwnershipError(RuntimeError):
    """The caller has no valid process ownership for this runtime store."""


class RuntimeBusyError(RuntimeOwnershipError):
    """Another runtime owner already holds this data directory."""


class ExtensionRuntimeLease(Protocol):
    """Keep process ownership until all owned workers and preparation work stop."""

    def hold_ownership(self) -> AbstractContextManager[None]:
        """Prevent close during one operation; do not re-enter or close from its body."""
        ...

    def close(self) -> None:
        """Release process ownership after active ownership contexts return."""
        ...


class ExtensionRuntimeOwnership(Protocol):
    """Acquire one native process lock for the complete runtime store."""

    def acquire_runtime(self) -> ExtensionRuntimeLease:
        """Return a new owned lease or fail without waiting for another owner."""
        ...
