# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep captured planning inputs and checked output separate from wire requests."""

from dataclasses import dataclass

from baqylau_extension_api.manifest.package import ExtensionManifest

from extensions.models.catalog import ExtensionCatalogSnapshot
from extensions.models.lifecycle_requests import LifecyclePlan
from extensions.models.lifecycle_selection import ExtensionIntent, RuntimePackageSelection
from extensions.models.manager import ManagerSnapshot
from extensions.models.record_migration import StoredRecordSchema
from extensions.models.runtime_candidates import RuntimePackageCandidate


@dataclass(frozen=True)
class SelectedPackage:
    """Pair selected package identity and settings with its retained declaration."""

    selection: RuntimePackageSelection
    manifest: ExtensionManifest


@dataclass(frozen=True)
class LifecyclePlanningState:
    """Capture one manager observation and its catalog before any user change."""

    manager: ManagerSnapshot
    catalog: ExtensionCatalogSnapshot
    selected: tuple[SelectedPackage, ...]
    record_schemas: tuple[StoredRecordSchema, ...] = ()


@dataclass(frozen=True)
class CheckedLifecyclePlan:
    """Keep private candidate settings out of the public preview response."""

    public: LifecyclePlan
    packages: tuple[RuntimePackageCandidate, ...]
    intents: tuple[ExtensionIntent, ...]
