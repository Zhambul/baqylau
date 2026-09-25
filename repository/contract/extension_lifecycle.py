# Copyright (c) 2026 Zhambyl Yermagambet
"""Store complete lifecycle operations without exposing a database connection."""

from typing import Protocol

from baqylau_extension_api.models.base import Identifier

from extensions.models.cleanup import ShutdownRecord
from extensions.models.lifecycle_operations import LifecycleCompletion, LifecycleOperation, LifecycleProposal
from extensions.models.lifecycle_state import LifecycleAdmission, LifecycleState, LifecycleWrite, ManagerClaim


class ExtensionLifecycleRepository(Protocol):
    """Fence manager generations and retain requested state and accepted history."""

    def read_extension_lifecycle(self) -> LifecycleState:
        """Read the stored head and all current settings in one transaction."""
        ...

    def claim_extension_manager(self, claim: ManagerClaim) -> LifecycleWrite:
        """Fence an old manager and interrupt its pending work after exclusive daemon startup."""
        ...

    def accept_extension_operation(self, proposal: LifecycleProposal, created_at: float) -> LifecycleAdmission:
        """Pin the candidate and requested state, or return replay, stale, or busy."""
        ...

    def finish_extension_operation(self, completion: LifecycleCompletion) -> LifecycleWrite:
        """Commit the stored candidate and settings on success; preserve them on failure."""
        ...

    def read_extension_operation(self, operation_id: Identifier) -> LifecycleOperation | None:
        """Read one persisted request and its current outcome."""
        ...

    def record_extension_shutdown(self, record: ShutdownRecord) -> bool:
        """Retain immutable observation for a known manager without changing active ownership."""
        ...
