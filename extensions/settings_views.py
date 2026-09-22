# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve accepted settings without including another scope's explicit choices."""

from baqylau_extension_api.models.base import ExtensionId
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.scopes import ExtensionScope

from extensions.lifecycle_plan_resources import LifecyclePlanningState
from extensions.models.settings import SettingsOverrides, capture_settings
from extensions.models.settings_requests import SettingsReadRequest
from extensions.models.settings_view import SettingsSnapshot
from extensions.settings_documents import settings_definition, validate_overrides
from extensions.settings_selection import settings_target


def read_settings_view(
    state: LifecyclePlanningState, extension_id: ExtensionId, request: SettingsReadRequest,
) -> SettingsSnapshot:
    """Select defaults, installation fallback, or one exact scope override.

    Returns:
        Accepted values and revisions, never an uncommitted candidate.

    """
    target = settings_target(state, extension_id, request.package_digest)
    definition = settings_definition(target, request.scope)
    validate_overrides(target, target.overrides)
    effective = capture_settings(target.package.manifest, target.overrides).for_scope(request.scope)
    return SettingsSnapshot(
        extension_info=target.package.selection.extension_info, scope=request.scope,
        lifecycle_revision=state.manager.lifecycle.revision, catalog_revision=state.catalog.revision,
        settings_revision=target.overrides.revision, selected_from_committed=target.selected_from_committed,
        pending_operation=state.manager.lifecycle.pending_operation, definition=definition,
        schemas=target.package.manifest.schemas, override=scoped_override(target.overrides, request.scope),
        effective=definition.defaults if effective is None else effective,
    )


def scoped_override(overrides: SettingsOverrides, scope: ExtensionScope) -> EncodedDocument | None:
    """Read only the explicit value at the exact selected scope.

    Returns:
        The override, or None to mean inheritance rather than an empty document.

    """
    if scope.kind == "installation":
        return overrides.installation
    return next((entry.settings for entry in overrides.scopes if entry.scope == scope), None)
