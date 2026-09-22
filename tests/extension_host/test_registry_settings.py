# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep full effective settings fixed across registry reads and scope selection."""

from dataclasses import replace

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.settings import SettingsDefinition
from baqylau_extension_api.models.documents import EncodedDocument
from baqylau_extension_api.models.scopes import InstallationScope, WorkspaceScope

from extensions.models.registry import RuntimeSettings, ScopedRuntimeSettings
from extensions.registry_package import RegistryPackage
from tests.extension_api import service_samples as peers
from tests.extension_host import registry_fixture as fixtures

WORKSPACE = WorkspaceScope(workspace_id="selected")
SETTINGS_REVISION = 4


def settings_package() -> RegistryPackage:
    """Use a complete fallback and a complete workspace value, without JSON merge.

    Returns:
        A package with an explicit fixed settings revision.

    """
    original = fixtures.peer(peers.BETA)
    default = EncodedDocument(schema_ref=peers.schema(peers.BETA).reference, json_text='"default"')
    definition = SettingsDefinition(defaults=default, scopes=("installation", "workspace"))
    settings = RuntimeSettings(
        revision=SETTINGS_REVISION, default=default, scopes=(ScopedRuntimeSettings(
            scope=WORKSPACE, settings=default.model_copy(update={"json_text": '"workspace"'}),
        ),),
    )
    manifest = original.manifest.model_copy(update={"settings": definition})
    return replace(original, manifest=manifest, settings=settings)


@pytest.mark.parametrize("workspace", [False, True])
def test_registry_selects_effective_settings(*, workspace: bool) -> None:
    """No consumer can replace the target's settings or its revision."""
    snapshot = fixtures.snapshot(settings_package())
    scope = WORKSPACE if workspace else InstallationScope()
    provider = snapshot.get_service_provider(peers.BETA, scope)
    assert provider is not None and provider.settings is not None
    assert provider.settings_revision == SETTINGS_REVISION
    expected = '"workspace"' if workspace else '"default"'
    assert provider.settings.json_text == expected


@pytest.mark.parametrize("change", ["duplicate", "invalid", "undeclared", "missing"])
def test_registry_rejects_bad_captured_settings(change: str) -> None:
    """Settings checks apply to every captured scope before publication."""
    package = settings_package()
    settings = package.settings
    if change == "duplicate":
        repeated = (*settings.scopes, *settings.scopes)
        settings = settings.model_copy(update={"scopes": repeated})
    if change == "invalid":
        assert settings.default is not None
        invalid = settings.default.model_copy(update={"json_text": "42"})
        settings = settings.model_copy(update={"default": invalid})
    if change == "undeclared":
        package = replace(package, manifest=peers.manifest(peers.BETA))
    if change == "missing":
        settings = RuntimeSettings()
    with pytest.raises(ExtensionContractError):
        fixtures.snapshot(replace(package, settings=settings))
