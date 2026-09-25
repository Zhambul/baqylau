# Copyright (c) 2026 Zhambyl Yermagambet
"""Store each extension's consecutive worker failures."""

from typing import Protocol

from extensions.models.extension_health import ExtensionHealth, HealthFailure


class ExtensionHealthStore(Protocol):
    """Count consecutive failures durably; a success clears them."""

    def read_health(self) -> tuple[ExtensionHealth, ...]:
        """Read every extension with a stored health row, in extension order."""
        ...

    def record_failure(self, failure: HealthFailure) -> ExtensionHealth:
        """Add one failure and return the new state; the limit makes it failed."""
        ...

    def record_success(self, owner: str, at: float) -> None:
        """Clear the consecutive failures of one extension."""
        ...
