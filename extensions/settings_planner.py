# Copyright (c) 2026 Zhambyl Yermagambet
"""Build one complete settings candidate without changing accepted overrides."""

from baqylau_extension_api.models.base import Identifier

from extensions.lifecycle_control_contract import LifecycleConflictError, LifecycleUnavailableError
from extensions.lifecycle_plan_resources import LifecyclePlanningState
from extensions.models.lifecycle_operations import LifecycleProposal
from extensions.models.lifecycle_selection import RuntimePackageSelection, RuntimeSelection, SettingsChange
from extensions.models.registry import ScopedRuntimeSettings
from extensions.models.settings import SettingsOverrides, capture_settings
from extensions.models.settings_requests import SettingsRequest, SettingsRequestOrigin
from extensions.settings_documents import settings_definition, validate_overrides
from extensions.settings_selection import SettingsTarget, settings_target


def plan_settings(
    state: LifecyclePlanningState, origin: SettingsRequestOrigin, operation_id: Identifier,
) -> LifecycleProposal:
    """Keep package choices and other owners unchanged while preparing new settings.

    Returns:
        A complete host-owned operation for normal manager admission.

    Raises:
        LifecycleUnavailableError: If the manager has no stored claim.

    """
    target = settings_target(state, origin.extension_id, origin.request.package_digest)
    overrides = changed_overrides(target, origin.request)
    manager_id = state.manager.lifecycle.manager_id
    if manager_id is None:
        message = "the extension manager has no stored ownership claim"
        raise LifecycleUnavailableError(message)
    return LifecycleProposal(
        operation_id=operation_id, manager_id=manager_id, expected_revision=origin.request.expected_revision,
        kind="settings", request_origin=origin,
        candidate=RuntimeSelection(
            runtime_revision=f"runtime-{operation_id}", catalog_revision=state.catalog.revision,
            packages=_selected_packages(state, target, overrides),
        ), settings_changes=(SettingsChange(
            extension_id=origin.extension_id, expected_revision=origin.request.expected_settings_revision,
            package_digest=origin.request.package_digest, settings=overrides,
        ),),
    )


def changed_overrides(target: SettingsTarget, request: SettingsRequest) -> SettingsOverrides:
    """Replace or remove one scope while preserving every other explicit choice.

    Returns:
        Checked raw choices at the next owner revision.

    Raises:
        LifecycleConflictError: If another settings edit changed the selected revision.

    """
    if target.overrides.revision != request.expected_settings_revision:
        message = "extension settings revision changed; read it before retrying"
        raise LifecycleConflictError(message)
    settings_definition(target, request.scope)
    installation = target.overrides.installation
    scopes = target.overrides.scopes
    if request.scope.kind == "installation":
        installation = request.document
    else:
        scopes = tuple(entry for entry in scopes if entry.scope != request.scope)
        if request.document is not None:
            scopes = (*scopes, ScopedRuntimeSettings(scope=request.scope, settings=request.document))
    overrides = SettingsOverrides(revision=target.overrides.revision + 1, installation=installation, scopes=scopes)
    validate_overrides(target, overrides)
    return overrides


def _selected_packages(
    state: LifecyclePlanningState, target: SettingsTarget, overrides: SettingsOverrides,
) -> tuple[RuntimePackageSelection, ...]:
    return tuple(
        RuntimePackageSelection(
            extension_info=entry.selection.extension_info, settings=capture_settings(entry.manifest, overrides),
        ) if entry.manifest.extension_id == target.package.manifest.extension_id else entry.selection
        for entry in state.selected
    )
