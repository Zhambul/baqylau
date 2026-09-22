# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep active reads and publication behind an explicit host protocol."""

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from extensions.models.lifecycle_selection import RuntimeSelection
from extensions.models.registry import RegistryPublication
from extensions.registry_snapshot import RuntimeSnapshot


class RegistryClosedError(RuntimeError):
    """The runtime is stopping and cannot admit another borrowed read."""


@dataclass(frozen=True)
class RegistryRead:
    """Bind a read to one compare-and-set revision and its borrowed capabilities."""

    revision: int
    snapshot: RuntimeSnapshot


class RegistryCommit(Protocol):
    """Commit the selected head without calling workers or re-entering the registry."""

    def commit_registry(self, selection: RuntimeSelection) -> bool:
        """Return true only after durable state accepts this exact runtime selection."""
        ...


class ExtensionRegistry(Protocol):
    """Prevent a normal replacement while calls still use the published worker set."""

    def read_snapshot(self) -> AbstractContextManager[RegistryRead]:
        """Hold one selection until the context ends; do not retain its capabilities."""
        ...

    def publish_snapshot(
        self, expected_revision: int, snapshot: RuntimeSnapshot, commit: RegistryCommit,
    ) -> RegistryPublication:
        """Publish only when the selection is valid, current, and has no active readers."""
        ...

    def close_registry(self, timeout_seconds: float) -> bool:
        """Stop new reads and wait up to the deadline for existing borrowed reads."""
        ...
