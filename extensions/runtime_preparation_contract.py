# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare complete worker sets without publishing them as the active runtime."""

from threading import Event
from typing import Protocol

from extensions.models.runtime_candidates import RuntimeCandidate
from extensions.models.runtime_resolution import RuntimeResolution
from extensions.registry_snapshot import RuntimeSnapshot


class RuntimePreparationError(RuntimeError):
    """A candidate cannot become a complete, ready runtime."""


class RuntimePreparationStoppedError(RuntimePreparationError):
    """The manager stopped before this complete candidate became ready."""


class PreparedExtensionRuntime(Protocol):
    """Own a ready candidate; the registry borrows its capabilities after publication."""

    @property
    def snapshot(self) -> RuntimeSnapshot:
        """The checked candidate, available only while this resource owner is open."""
        ...

    @property
    def resolution(self) -> RuntimeResolution | None:
        """The complete converted overrides, absent when no migration was needed."""
        ...

    def close(self) -> None:
        """Close an unpublished or fully drained set; never close an active borrowed set."""
        ...


class ExtensionRuntimePreparation(Protocol):
    """Prepare fresh workers outside the engine and database transaction boundaries."""

    def prepare_runtime(self, selection: RuntimeCandidate, stop_requested: Event) -> PreparedExtensionRuntime:
        """Return the complete ready set or release every resource from this attempt."""
        ...
