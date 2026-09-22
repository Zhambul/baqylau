# Copyright (c) 2026 Zhambyl Yermagambet
"""Define pure extension-owned records and feed output behind one protocol."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from baqylau_extension_api.models.projection_transforms import ProjectionTransformRequest, ProjectionTransformResult
    from baqylau_extension_api.models.projections import (
        ProjectionReadSet,
        ProjectionRequest,
        ProjectionResult,
        ProjectionSelectionRequest,
    )


@runtime_checkable
class ExtensionProjector(Protocol):
    """Build derived data from captured facts and records without live effects."""

    def select_records(self, selection_request: ProjectionSelectionRequest) -> ProjectionReadSet:
        """Select captured keys; the host reads them after this pure call."""

    def project(self, projection_request: ProjectionRequest) -> ProjectionResult:
        """Return one complete proposal; the host owns all writes and cursors."""


@runtime_checkable
class ExtensionProjectionTransformer(Protocol):
    """Change proposed derived data while retaining required core execution state."""

    def transform(self, projection_request: ProjectionTransformRequest) -> ProjectionTransformResult:
        """Return explicit ordered operations without writing storage or live state."""
