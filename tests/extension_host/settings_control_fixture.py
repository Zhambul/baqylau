# Copyright (c) 2026 Zhambyl Yermagambet
"""Drive the real settings service and the test-owned engine publication boundary."""

from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.scopes import ExtensionScope, InstallationScope, WorkspaceScope

from extensions.models.settings_requests import SettingsReadRequest, SettingsRequest
from extensions.models.settings_view import SettingsSnapshot
from extensions.settings_control import SettingsControl
from tests.extension_host import lifecycle_control_fixture, package_fixture

OWNER = package_fixture.OWNER
INSTALLATION = InstallationScope()
WORKSPACE = WorkspaceScope(workspace_id="workspace-one")
OTHER_WORKSPACE = WorkspaceScope(workspace_id="workspace-two")


def service(case: lifecycle_control_fixture.ControlHost) -> SettingsControl:
    """Use the same manager, metadata repository, and policy as lifecycle controls.

    Returns:
        The production settings service, with no storage double.

    """
    return SettingsControl(case.host.controller, case.control.catalog, case.control.policy)


def snapshot(case: lifecycle_control_fixture.ControlHost, scope: ExtensionScope = INSTALLATION) -> SettingsSnapshot:
    """Read accepted settings for one exact scope.

    Returns:
        The selected schema, override, effective document, and revisions.

    """
    return service(case).read_settings(OWNER, SettingsReadRequest(scope=scope))


def request(
    case: lifecycle_control_fixture.ControlHost, key: str, document: str | None,
    scope: ExtensionScope = INSTALLATION,
) -> SettingsRequest:
    """Select request revisions as a client does, not from direct database access.

    Returns:
        A complete replacement request or explicit reset.

    """
    current = snapshot(case, scope)
    return SettingsRequest(
        request_id=key, expected_revision=current.lifecycle_revision,
        expected_catalog_revision=current.catalog_revision, expected_settings_revision=current.settings_revision,
        package_digest=current.extension_info.package_digest, scope=scope,
        document=None if document is None else EncodedDocument(
            schema_ref=current.definition.defaults.schema_ref, json_text=document,
        ),
    )


def save(
    case: lifecycle_control_fixture.ControlHost, key: str, document: str | None,
    scope: ExtensionScope = INSTALLATION,
) -> SettingsRequest:
    """Complete an accepted operation at the actual manager boundary.

    Returns:
        The original request for exact retry and conflict tests.

    """
    selected = request(case, key, document, scope)
    assert service(case).change_settings(OWNER, selected).status == "accepted"
    case.host.finish()
    return selected


def enable(case: lifecycle_control_fixture.ControlHost) -> None:
    """Enable the fixture only through a checked lifecycle request."""
    case.control.change_lifecycle(OWNER, case.request("enable", "enable-settings-owner"))
    case.host.finish()


def require_runtime_settings(case: lifecycle_control_fixture.ControlHost, current: SettingsSnapshot) -> None:
    """Read the actual registry value after settings publication."""
    registry = case.host.runtime.preparation.registry
    with registry.read_snapshot() as selected:
        package = selected.snapshot.packages[0]
        assert package.settings.default == current.effective
