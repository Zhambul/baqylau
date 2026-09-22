# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep pure transform calls independent of process transport."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from baqylau_extension_api.models.raw_transforms import RawTransformResult
    from baqylau_extension_api.models.transforms import (
        CanonicalTransformRequest,
        CanonicalTransformResult,
        RawTransformRequest,
    )


@runtime_checkable
class ExtensionRawTransformer(Protocol):
    """Propose changes to derived raw input without changing stored evidence."""

    def transform(self, request: RawTransformRequest) -> RawTransformResult:
        """Return one batch of operations without calling live services."""


@runtime_checkable
class ExtensionCanonicalTransformer(Protocol):
    """Propose changes to typed canonical candidates before acceptance."""

    def transform(self, canonical_request: CanonicalTransformRequest) -> CanonicalTransformResult:
        """Return one validated operation batch for this canonical boundary."""
