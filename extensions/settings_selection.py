# Copyright (c) 2026 Zhambyl Yermagambet
"""Select retained active declarations and keep package reload out of settings edits."""

from dataclasses import dataclass

from baqylau_extension_api.models.base import Digest, ExtensionId

from extensions.lifecycle_control_contract import LifecycleConflictError, LifecycleRequestError
from extensions.lifecycle_plan_reads import discovered_package
from extensions.lifecycle_plan_resources import LifecyclePlanningState, SelectedPackage
from extensions.models.settings import SettingsOverrides


@dataclass(frozen=True)
class SettingsTarget:
    """Bind one declaration to its raw accepted overrides."""

    package: SelectedPackage
    overrides: SettingsOverrides
    selected_from_committed: bool


def settings_target(
    state: LifecyclePlanningState, extension_id: ExtensionId, package_digest: Digest | None,
) -> SettingsTarget:
    """Use committed bytes first; only an inactive package selects current discovery.

    Returns:
        One checked owner declaration and its accepted raw choices.

    Raises:
        LifecycleConflictError: If an enabled package has another selected digest.
        LifecycleRequestError: If the package declares no settings.

    """
    committed = next((
        entry for entry in state.selected if entry.manifest.extension_id == extension_id
    ), None)
    selected = committed or discovered_package(state, extension_id, package_digest)
    if package_digest is not None and selected.selection.extension_info.package_digest != package_digest:
        message = "settings edits must use the committed package; reload selects new bytes"
        raise LifecycleConflictError(message)
    if selected.manifest.settings is None:
        message = "the selected extension has no settings declaration"
        raise LifecycleRequestError(message)
    overrides = next((entry.settings for entry in state.manager.lifecycle.settings
                      if entry.extension_id == extension_id), SettingsOverrides())
    return SettingsTarget(selected, overrides, committed is not None)
