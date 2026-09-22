# Copyright (c) 2026 Zhambyl Yermagambet
"""Use explicit test-only memory commits in registry protocol unit tests."""

from extensions.models.lifecycle_selection import RuntimeSelection
from extensions.models.registry import RegistryPublication
from extensions.registry import ActiveExtensionRegistry
from extensions.registry_contract import RegistryCommit
from extensions.registry_snapshot import RuntimeSnapshot


class MemoryCommit(RegistryCommit):
    """Accept a checked unit-test selection without claiming durable storage."""

    def commit_registry(self, selection: RuntimeSelection) -> bool:
        """Accept the unit-test publication.

        Returns:
            True after the normal selection model checks pass.

        """
        RuntimeSelection.model_validate(selection)
        return True


MEMORY_COMMIT = MemoryCommit()


class MemoryRegistry(ActiveExtensionRegistry):
    """Keep standalone unit tests explicit about their lack of a durable commit."""

    def publish_snapshot(
        self, expected_revision: int, snapshot: RuntimeSnapshot, commit: RegistryCommit = MEMORY_COMMIT,
    ) -> RegistryPublication:
        """Call the real implementation with a test-only default commit adapter.

        Returns:
            The normal reader and revision checks from the production registry.

        """
        return super().publish_snapshot(expected_revision, snapshot, commit)
