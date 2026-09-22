# Copyright (c) 2026 Zhambyl Yermagambet
"""Separate live source reads from pure recorded-input translation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from baqylau_extension_api.models.source_results import SourcePlan, SourceReadResult, SourceReleaseResult
    from baqylau_extension_api.models.sources import SourceContext, SourceReadRequest, SourceReleaseRequest
    from baqylau_extension_api.models.translation_inputs import ExtensionTranslationRequest
    from baqylau_extension_api.models.translation_results import ExtensionTranslationResult


@runtime_checkable
class ExtensionSources(Protocol):
    """Describe, read, and release owned sources without writing host progress."""

    def describe(self, source_context: SourceContext) -> SourcePlan:
        """Describe all current sources for one selected scope."""

    def read(self, source_request: SourceReadRequest) -> SourceReadResult:
        """Read after a committed source position."""

    def release(self, release_request: SourceReleaseRequest) -> SourceReleaseResult:
        """Release a source or a complete scope and report pending cleanup."""


@runtime_checkable
class ExtensionTranslator(Protocol):
    """Decode a declared source from immutable input and captured state."""

    def translate(self, translation_request: ExtensionTranslationRequest) -> ExtensionTranslationResult:
        """Return complete decisions and proposed next state without live effects."""
