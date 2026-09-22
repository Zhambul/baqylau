# Copyright (c) 2026 Zhambyl Yermagambet
"""Preserve default inheritance and settings for disabled extensions."""

from pathlib import Path

import pytest
from baqylau_extension_api.models.scopes import InstallationScope

from extensions.models.registry import ScopedRuntimeSettings
from extensions.models.settings import SettingsOverrides, capture_settings
from tests.extension_host import catalog_fixture, lifecycle_fixture as fixtures, lifecycle_settings_fixture as settings


def test_disabled_package_can_save_settings(tmp_path: Path) -> None:
    """Saving disabled settings does not silently enable that package."""
    store = fixtures.claimed_repository(tmp_path)
    package = fixtures.install_package(tmp_path)
    proposed = settings.settings_proposal(store, package, settings.changed_overrides(package))
    proposed = proposed.model_copy(update={
        "candidate": proposed.candidate.model_copy(update={"packages": ()}),
        "intents": (proposed.intents[0].model_copy(update={"enabled": False}),),
    })
    fixtures.commit(store, proposed)
    state = store.read_extension_lifecycle()
    assert state.committed_runtime is not None and not state.committed_runtime.packages
    assert not state.intents[0].enabled
    assert state.settings[0].settings == proposed.settings_changes[0].settings


def test_updated_defaults_do_not_change_overrides(tmp_path: Path) -> None:
    """An explicit choice survives a default change, but an inherited value follows it."""
    package = fixtures.install_package(tmp_path)
    manifest = catalog_fixture.repository(tmp_path).retained_extension_manifest(package.extension_info.package_digest)
    assert manifest is not None and manifest.settings is not None
    changed = manifest.settings.model_copy(update={
        "defaults": manifest.settings.defaults.model_copy(update={"json_text": '"new default"'}),
    })
    manifest = manifest.model_copy(update={"settings": changed})
    assert capture_settings(manifest, SettingsOverrides()).default == changed.defaults
    assert capture_settings(manifest, settings.changed_overrides(package)).default != changed.defaults


def test_installation_override_has_one_location(tmp_path: Path) -> None:
    """Installation values cannot also appear in the exact-scope override list."""
    package = fixtures.install_package(tmp_path)
    assert package.settings.default is not None
    with pytest.raises(ValueError, match="installation override field"):
        SettingsOverrides(scopes=(ScopedRuntimeSettings(scope=InstallationScope(), settings=package.settings.default),))
