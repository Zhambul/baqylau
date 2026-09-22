# Copyright (c) 2026 Zhambyl Yermagambet
"""Read retained active declarations independently of mutable package discovery."""

from baqylau_extension_api.manifest.package import ExtensionManifest
from baqylau_extension_api.models.base import Digest, ExtensionId
from baqylau_extension_api.models.lifecycle import ExtensionInfo
from baqylau_extension_api.versions import API_VERSION

from extensions.lifecycle_control_contract import (
    LifecycleConflictError,
    LifecycleRequestError,
    LifecycleUnavailableError,
)
from extensions.lifecycle_plan_resources import LifecyclePlanningState, SelectedPackage
from extensions.models.control_requests import ControlRevisions
from extensions.models.lifecycle_selection import RuntimePackageSelection
from extensions.models.manager import ManagerSnapshot
from extensions.models.settings import SettingsOverrides, capture_settings
from repository.contract.extension_catalog import ExtensionCatalogRepository


def planning_state(
    manager: ManagerSnapshot, catalog: ExtensionCatalogRepository, request: ControlRevisions,
) -> LifecyclePlanningState:
    """Read metadata only, with no feature call or resource preparation.

    Returns:
        A fixed planning input which admission must recheck transactionally.

    Raises:
        LifecycleUnavailableError: If the manager has stopped or cannot accept work.
        LifecycleConflictError: If the selected state has changed.

    """
    if manager.phase in {"preparing", "awaiting_boundary"} or manager.cleanup_pending:
        message = "another extension lifecycle change or cleanup is pending"
        raise LifecycleConflictError(message)
    if manager.phase != "running":
        message = "the extension manager is not ready for another lifecycle change"
        raise LifecycleUnavailableError(message)
    state = read_planning_state(manager, catalog)
    if (
        manager.lifecycle.revision != request.expected_revision
        or state.catalog.revision != request.expected_catalog_revision
    ):
        message = "extension lifecycle or catalog revision changed; read it before retrying"
        raise LifecycleConflictError(message)
    return state


def read_planning_state(manager: ManagerSnapshot, catalog: ExtensionCatalogRepository) -> LifecyclePlanningState:
    """Read committed metadata without requiring idle mutation admission.

    Returns:
        Current stored selections and discovery; a read does not claim worker health.

    """
    snapshot = catalog.read_extension_catalog()
    selected = manager.lifecycle.committed_runtime
    packages = () if selected is None else selected.packages
    return LifecyclePlanningState(manager, snapshot, tuple(_retained(package, catalog) for package in packages))


def discovered_package(
    state: LifecyclePlanningState, extension_id: ExtensionId, package_digest: Digest | None,
) -> SelectedPackage:
    """Use only the valid current catalog row and the caller's exact digest.

    Returns:
        Checked selected bytes and current explicit settings overrides.

    Raises:
        LifecycleRequestError: If the package is absent, invalid, or ambiguous.
        LifecycleConflictError: If the selected bytes no longer match discovery.

    """
    entries = tuple(entry for entry in state.catalog.entries
                    if entry.manifest is not None and entry.manifest.extension_id == extension_id)
    if len(entries) != 1 or entries[0].issue is not None:
        message = "extension selection requires one valid discovered package"
        raise LifecycleRequestError(message)
    entry = entries[0]
    if package_digest is not None and entry.package_digest != package_digest:
        message = "selected extension bytes differ from the current catalog"
        raise LifecycleConflictError(message)
    return _discovered_selection(state, entry.manifest, entry.package_digest)


def _discovered_selection(
    state: LifecyclePlanningState, manifest: ExtensionManifest | None, package_digest: str | None,
) -> SelectedPackage:
    if manifest is None or package_digest is None:
        message = "valid discovery metadata is incomplete"
        raise LifecycleRequestError(message)
    overrides = next((entry.settings for entry in state.manager.lifecycle.settings
                      if entry.extension_id == manifest.extension_id), SettingsOverrides())
    return SelectedPackage(RuntimePackageSelection(
        extension_info=ExtensionInfo(
            extension_id=manifest.extension_id, package_version=manifest.package_version,
            api_version=API_VERSION, package_digest=package_digest,
        ), settings=capture_settings(manifest, overrides),
    ), manifest)


def _retained(package: RuntimePackageSelection, catalog: ExtensionCatalogRepository) -> SelectedPackage:
    manifest = catalog.retained_extension_manifest(package.extension_info.package_digest)
    if manifest is None:
        message = "the committed extension declaration is unavailable"
        raise LifecycleRequestError(message)
    return SelectedPackage(package, manifest)
