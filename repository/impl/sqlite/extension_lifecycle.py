# Copyright (c) 2026 Zhambyl Yermagambet
"""Store complete extension lifecycle changes through the public repository contract."""

from dataclasses import dataclass

from baqylau_extension_api.models.base import Identifier
from pydantic import TypeAdapter

from extensions.models.cleanup import ShutdownRecord
from extensions.models.lifecycle_operations import LifecycleCompletion, LifecycleOperation, LifecycleProposal, Timestamp
from extensions.models.lifecycle_state import LifecycleAdmission, LifecycleState, LifecycleWrite, ManagerClaim
from repository.contract.extension_lifecycle import ExtensionLifecycleRepository
from repository.impl.sqlite import (
    extension_lifecycle_accept as admission,
    extension_lifecycle_claim as claims,
    extension_lifecycle_finish as outcomes,
    extension_lifecycle_reads as reads,
    extension_shutdown,
)
from repository.impl.sqlite.connection import SqliteDatabase


@dataclass(frozen=True)
class SqliteExtensionLifecycleRepository(ExtensionLifecycleRepository):
    """Own all transaction boundaries; no operation here calls extension code."""

    database: SqliteDatabase

    def read_extension_lifecycle(self) -> LifecycleState:
        """Read current stored state without claiming that a process is ready.

        Returns:
            One complete lifecycle and settings snapshot.

        """
        with self.database.read() as connection:
            return reads.read_state(connection)

    def claim_extension_manager(self, claim: ManagerClaim) -> LifecycleWrite:
        """Fence old completions after exclusive daemon startup.

        Returns:
            New manager state or the unchanged state on revision conflict.

        """
        checked = ManagerClaim.model_validate(claim)
        with self.database.write() as connection:
            return claims.claim_manager(connection, checked)

    def accept_extension_operation(self, proposal: LifecycleProposal, created_at: float) -> LifecycleAdmission:
        """Reserve immutable candidate bytes and requested state together.

        Returns:
            Explicit accepted, replayed, stale, or busy admission.

        """
        checked = LifecycleProposal.model_validate(proposal)
        timestamp = TypeAdapter(Timestamp).validate_python(created_at)
        with self.database.write() as connection:
            return admission.accept_operation(connection, checked, timestamp)

    def finish_extension_operation(self, completion: LifecycleCompletion) -> LifecycleWrite:
        """Commit the exact reserved candidate or preserve the prior runtime on failure.

        Returns:
            The persisted outcome, or unchanged state for a stale completion.

        """
        checked = LifecycleCompletion.model_validate(completion)
        with self.database.write() as connection:
            return outcomes.finish_operation(connection, checked)

    def read_extension_operation(self, operation_id: Identifier) -> LifecycleOperation | None:
        """Read an operation without mutating its state or checking package files.

        Returns:
            The retained request and outcome, if present.

        """
        with self.database.read() as connection:
            return reads.read_operation(connection, operation_id)

    def record_extension_shutdown(self, record: ShutdownRecord) -> bool:
        """Commit uncertainty before the manager releases native process ownership.

        Returns:
            False if this manager has no stored claim or accepted operation.

        """
        checked = ShutdownRecord.model_validate(record)
        with self.database.write() as connection:
            return extension_shutdown.append_record(connection, checked)
