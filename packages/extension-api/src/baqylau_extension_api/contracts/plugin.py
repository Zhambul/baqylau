# Copyright (c) 2026 Zhambyl Yermagambet
"""Compose typed extension capabilities behind one protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from baqylau_extension_api.contracts import migrations as migration_contracts, observers
from baqylau_extension_api.contracts.lifecycle import ExtensionLifecycle
from baqylau_extension_api.contracts.operations import ExtensionCommands, ExtensionQueries
from baqylau_extension_api.contracts.presentation import ExtensionTerminalPresenter
from baqylau_extension_api.contracts.processing import ExtensionCanonicalTransformer, ExtensionRawTransformer
from baqylau_extension_api.contracts.projection import ExtensionProjectionTransformer, ExtensionProjector
from baqylau_extension_api.contracts.sources import ExtensionSources, ExtensionTranslator

if TYPE_CHECKING:
    from baqylau_extension_api.contracts.services import ExtensionHostServices
    from baqylau_extension_api.models.lifecycle import ExtensionInfo


@dataclass(frozen=True)
class ExtensionCapabilities:
    """Group supported capabilities without empty optional handlers."""

    lifecycle: ExtensionLifecycle
    sources: ExtensionSources | None = None
    translator: ExtensionTranslator | None = None
    raw_transformer: ExtensionRawTransformer | None = None
    canonical_transformer: ExtensionCanonicalTransformer | None = None
    projector: ExtensionProjector | None = None
    projection_transformer: ExtensionProjectionTransformer | None = None
    queries: ExtensionQueries | None = None
    commands: ExtensionCommands | None = None
    terminal: ExtensionTerminalPresenter | None = None
    migrations: migration_contracts.ExtensionMigrations | None = None
    observer: observers.ExtensionObserver | None = None


@runtime_checkable
class ExtensionPlugin(Protocol):
    """Describe a backend package without exposing its implementation."""

    @property
    def extension_info(self) -> ExtensionInfo:
        """The package identity that the host must verify."""

    @property
    def capabilities(self) -> ExtensionCapabilities:
        """The typed capabilities implemented by this package."""


class ExtensionFactory(Protocol):
    """Describe the package entry called only inside an extension worker."""

    def __call__(self, services: ExtensionHostServices) -> ExtensionPlugin:
        """Build a package instance with the permitted host services."""
