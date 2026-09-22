# Copyright (c) 2026 Zhambyl Yermagambet
"""Separate manager control from the engine's short publication boundary."""

from typing import Protocol

from baqylau_extension_api.models.base import Identifier

from extensions.models.lifecycle_operations import LifecycleOperation, LifecycleProposal
from extensions.models.lifecycle_state import LifecycleAdmission
from extensions.models.manager import ManagerProgress, ManagerSnapshot


class ManagerStateError(RuntimeError):
    """The manager cannot accept another operation in its current state."""


class ManagerCleanupError(RuntimeError):
    """Runtime resources still require cleanup; process ownership remains held."""


class ExtensionRuntimeBoundary(Protocol):
    """Let the engine publish prepared work between complete processing batches."""

    def publish_ready(self) -> ManagerProgress:
        """Try one prepared change; keep a busy candidate owned for a later attempt."""
        ...


class ExtensionManager(ExtensionRuntimeBoundary, Protocol):
    """Own accepted host proposals, workers, publication, and process lifetime."""

    def read_state(self) -> ManagerSnapshot:
        """Read stored intent and observed manager state without a worker call."""
        ...

    def read_operation(self, operation_id: Identifier) -> LifecycleOperation | None:
        """Read the durable outcome of an accepted operation."""
        ...

    def submit_operation(self, proposal: LifecycleProposal) -> LifecycleAdmission:
        """Accept a complete host-built proposal and prepare it outside the caller."""
        ...

    def retry_cleanup(self) -> None:
        """Retry retained cleanup without repeating a user command or preparation."""
        ...

    def close(self) -> None:
        """Stop admission and retain ownership until all owned work has stopped."""
        ...
