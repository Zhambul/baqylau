# Copyright (c) 2026 Zhambyl Yermagambet
"""Return explicit lifecycle progress without any worker or database side effects."""

from dataclasses import dataclass, field

from extensions.manager_contract import ExtensionRuntimeBoundary
from extensions.models.manager import ManagerProgress


@dataclass
class BoundaryDouble(ExtensionRuntimeBoundary):
    """Control readiness and injected publication failure in engine unit tests."""

    progress: ManagerProgress = field(default_factory=lambda: ManagerProgress(status="idle", registry_revision=0))
    calls: int = 0
    failures: int = 0
    fail_publication: bool = False

    def publish_ready(self) -> ManagerProgress:
        """Return selected state without modifying the queue.

        Returns:
            The selected typed engine-boundary result.

        Raises:
            RuntimeError: If the test selects a stored publication failure.

        """
        self.calls += 1
        if self.fail_publication:
            message = "test publication failure"
            raise RuntimeError(message)
        return self.progress

    def record_failure(self) -> None:
        """Count engine error reports without changing the selected boundary result."""
        self.failures += 1
