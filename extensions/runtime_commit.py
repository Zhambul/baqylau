# Copyright (c) 2026 Zhambyl Yermagambet
"""Join the prepared registry selection with its exact stored lifecycle operation."""

from dataclasses import dataclass

from extensions.models.lifecycle_operations import LifecycleCompletion, LifecycleOperation
from extensions.models.lifecycle_selection import RuntimeSelection
from extensions.models.runtime_candidates import MigratingRuntimeSelection
from extensions.models.runtime_resolution import RuntimeResolution
from extensions.registry_contract import RegistryCommit
from repository.contract.extension_lifecycle import ExtensionLifecycleRepository


@dataclass(frozen=True)
class StoredRegistryCommit(RegistryCommit):
    """Run only at the exclusive registry boundary after all worker preparation."""

    repository: ExtensionLifecycleRepository
    operation: LifecycleOperation
    completed_at: float
    resolution: RuntimeResolution | None = None

    def commit_registry(self, selection: RuntimeSelection) -> bool:
        """Reject a substituted selection before committing any database state.

        Returns:
            True only when the exact accepted runtime is the stored committed head.

        Raises:
            ValueError: If the registry selection differs from the accepted candidate.

        """
        candidate = self.operation.proposal.candidate
        expected = candidate
        if isinstance(candidate, MigratingRuntimeSelection) and self.resolution is not None:
            self.resolution.validate_candidate(candidate)
            expected = self.resolution.runtime
        if self.operation.status != "preparing" or selection != expected:
            message = "registry selection does not match the accepted lifecycle candidate"
            raise ValueError(message)
        outcome = self.repository.finish_extension_operation(LifecycleCompletion(
            manager_id=self.operation.proposal.manager_id, operation_id=self.operation.proposal.operation_id,
            expected_revision=self.operation.accepted_revision, completed_at=self.completed_at,
            resolution=self.resolution,
        ))
        return outcome.accepted and outcome.state.committed_runtime == selection
