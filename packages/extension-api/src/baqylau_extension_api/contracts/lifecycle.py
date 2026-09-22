# Copyright (c) 2026 Zhambyl Yermagambet
"""Separate worker resource management from its optional capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from baqylau_extension_api.models.lifecycle import (
        ActivationRequest,
        ActivationResult,
        DeactivationRequest,
        DeactivationResult,
    )


@runtime_checkable
class ExtensionLifecycle(Protocol):
    """Prepare and release resources for one runtime revision."""

    def activate(self, request: ActivationRequest) -> ActivationResult:
        """Prepare resources without switching the host's active revision."""

    def deactivate(self, request: DeactivationRequest) -> DeactivationResult:
        """Release resources and report jobs that still need reconciliation."""
