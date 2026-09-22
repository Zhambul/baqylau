# Copyright (c) 2026 Zhambyl Yermagambet
"""Hold preparation at a known boundary without replacing the manager or store."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from threading import Event

from extensions.manager_contract import ExtensionManager
from extensions.models.runtime_candidates import RuntimeCandidate
from extensions.runtime_preparation_contract import ExtensionRuntimePreparation, PreparedExtensionRuntime


@dataclass
class ControlledPreparation(ExtensionRuntimePreparation):
    """Delegate to the real preparer after the test releases its admission check."""

    delegate: ExtensionRuntimePreparation
    entered: Event = field(default_factory=Event)
    released: Event = field(default_factory=Event)
    revisions: list[str] = field(default_factory=list)
    stop_request: Event | None = None

    def prepare_runtime(self, selection: RuntimeCandidate, stop_requested: Event) -> PreparedExtensionRuntime:
        """Record exactly one call and keep its thread separate from the caller.

        Returns:
            The real owned runtime after release.

        Raises:
            TimeoutError: If the test does not release its preparation boundary.

        """
        self.revisions.append(selection.runtime_revision)
        self.stop_request = stop_requested
        self.entered.set()
        if not self.released.wait(5):
            message = "test did not release runtime preparation"
            raise TimeoutError(message)
        return self.delegate.prepare_runtime(selection, stop_requested)

    def close_when_stopped(self, manager: ExtensionManager, pool: ThreadPoolExecutor) -> None:
        """Release held preparation only after shutdown has set its stop request."""
        pending = pool.submit(manager.close)
        assert self.stop_request is not None and self.stop_request.wait(5)
        self.released.set()
        pending.result(timeout=5)
