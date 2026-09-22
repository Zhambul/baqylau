# Copyright (c) 2026 Zhambyl Yermagambet
"""Drive real candidate workers with independently built feature migration code."""

from pathlib import Path
from typing import Literal

from baqylau_extension_api.models.lifecycle import ActivationRequest
from baqylau_extension_api.models.scopes import ExtensionScope, InstallationScope

from extensions.models.settings_requests import SettingsReadRequest, SettingsRequest
from extensions.models.settings_view import SettingsSnapshot
from tests.extension_api import migration_samples
from tests.extension_host import environment_fixture, lifecycle_control_fixture as controls, package_fixture
from tests.extension_host.settings_control_fixture import service

OWNER = migration_samples.OWNER
INSTALLATION = InstallationScope()
CODE = Path(__file__).parents[1] / "extension_api" / "migration_example.py"
ACTIVE = "migration-active"
MIGRATING = "migration-worker"
ACTIVATION = "migration-activation.json"


def write_package(directory: Path, wheels: Path) -> Path:
    """Write backend, schemas, dependencies, and feature markers outside the checkout.

    Returns:
        Mutable source for a later package reload.

    """
    source = environment_fixture.write_package(directory, wheels)
    select_version(source, 1)
    code = CODE.read_text(encoding="utf-8")
    code = f"import os\nfrom pathlib import Path\n{code}"
    code = code.replace("return lifecycle_models.ActivationReady", (
        f"Path({str(directory / ACTIVE)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        f"        Path({str(directory / ACTIVATION)!r}).write_text(request.model_dump_json(), encoding='utf-8')\n"
        "        return lifecycle_models.ActivationReady"
    ))
    code = code.replace("request = settings_request", (
        f"Path({str(directory / MIGRATING)!r}).write_text(str(os.getpid()), encoding='utf-8')\n"
        "        request = settings_request"
    ))
    package_fixture.write_file(source, "migration_backend.py", code.encode())
    return source


def select_version(source: Path, version: int) -> None:
    """Select the exact forward or declared reverse schema path."""
    previous = package_fixture.read_manifest(source)
    manifest = migration_samples.manifest(downgrade=version == 1)
    assert previous.backend is not None and manifest.backend is not None and manifest.settings is not None
    package_fixture.save_manifest(source, manifest.model_copy(update={
        "package_version": f"{version}.0.0",
        "backend": manifest.backend.model_copy(update={"environment": previous.backend.environment}),
        "settings": manifest.settings.model_copy(update={"scopes": ("installation", "workspace")}),
    }))


def read(case: controls.ControlHost, scope: ExtensionScope = INSTALLATION) -> SettingsSnapshot:
    """Read only accepted settings through the production request service.

    Returns:
        The exact current package, revisions, defaults, and override.

    """
    return service(case).read_settings(OWNER, SettingsReadRequest(scope=scope))


def save(case: controls.ControlHost, encoded: str, scope: ExtensionScope = INSTALLATION) -> None:
    """Save one valid old-schema value before changing package bytes."""
    current = read(case, scope)
    request = SettingsRequest(
        request_id=f"save-{current.settings_revision}", package_digest=current.extension_info.package_digest,
        expected_revision=current.lifecycle_revision, expected_catalog_revision=current.catalog_revision,
        expected_settings_revision=current.settings_revision, scope=scope,
        document=current.effective.model_copy(update={"json_text": encoded}),
    )
    assert service(case).change_settings(OWNER, request).status == "accepted"
    case.host.finish()


def activate(case: controls.ControlHost, action: Literal["enable", "reload"] = "enable") -> None:
    """Start or reload only through checked user requests."""
    request = case.request(action, f"{action}-{read(case).lifecycle_revision}", OWNER)
    assert case.control.change_lifecycle(OWNER, request).status == "accepted"
    case.host.finish()


def activated(directory: Path) -> ActivationRequest:
    """Read what the real feature process received after conversion.

    Returns:
        The actual activation request, not the planned settings.

    """
    return ActivationRequest.model_validate_json((directory / ACTIVATION).read_bytes())
