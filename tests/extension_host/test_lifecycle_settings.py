# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep defaults, raw overrides, and effective runtime settings distinct."""

from pathlib import Path

import pytest

from extensions.models.settings import SettingsOverrides
from tests.extension_host import lifecycle_fixture as fixtures, lifecycle_settings_fixture as settings


def test_settings_change_is_atomic_with_runtime(tmp_path: Path) -> None:
    """Pending settings stay out of current reads until the candidate succeeds."""
    store = fixtures.claimed_repository(tmp_path)
    package = fixtures.install_package(tmp_path)
    proposed = settings.settings_proposal(store, package, settings.changed_overrides(package))
    admitted = store.accept_extension_operation(proposed, fixtures.NOW)
    assert admitted.operation is not None and not admitted.state.settings
    finished = store.finish_extension_operation(fixtures.completion(admitted.operation))
    assert finished.state.committed_runtime == proposed.candidate
    assert finished.state.settings[0].settings == proposed.settings_changes[0].settings
    assert fixtures.repository(tmp_path).read_extension_lifecycle() == finished.state


def test_failed_settings_keep_old_values(tmp_path: Path) -> None:
    """Preparation failure retains both prior overrides and prior effective values."""
    store = fixtures.claimed_repository(tmp_path)
    package = fixtures.install_package(tmp_path)
    fixtures.commit(store, fixtures.proposal(store, package))
    before = store.read_extension_lifecycle()
    admitted = store.accept_extension_operation(
        settings.settings_proposal(store, package, settings.changed_overrides(package)), fixtures.NOW,
    )
    assert admitted.operation is not None
    finished = settings.fail_preparation(store, admitted.operation)
    assert finished.state.settings == before.settings
    assert finished.state.committed_runtime == before.committed_runtime


def test_reset_preserves_default_inheritance(tmp_path: Path) -> None:
    """Reset removes the installation choice but captures the manifest default."""
    store = fixtures.claimed_repository(tmp_path)
    package = fixtures.install_package(tmp_path)
    fixtures.commit(store, settings.settings_proposal(store, package, settings.changed_overrides(package)))
    reset = settings.settings_proposal(store, package, SettingsOverrides(revision=2), "reset")
    fixtures.commit(store, reset)
    state = store.read_extension_lifecycle()
    assert state.settings[0].settings.installation is None
    assert state.committed_runtime == reset.candidate
    captured = reset.candidate.packages[0].settings
    assert captured.default == package.settings.default


def test_settings_compare_owner_revision(tmp_path: Path) -> None:
    """A current lifecycle revision cannot hide a stale settings edit."""
    store = fixtures.claimed_repository(tmp_path)
    package = fixtures.install_package(tmp_path)
    proposed = settings.settings_proposal(store, package, settings.changed_overrides(package))
    fixtures.commit(store, proposed)
    repeated = settings.settings_proposal(store, package, settings.changed_overrides(package), "stale-settings")
    assert store.accept_extension_operation(repeated, fixtures.NOW).status == "stale"
    assert store.read_extension_operation(repeated.operation_id) is None


@pytest.mark.parametrize("change", ["captured", "schema", "owner"])
def test_bad_settings_do_not_change_state(tmp_path: Path, change: str) -> None:
    """Reject forged effective settings and invalid raw choices before admission."""
    store = fixtures.claimed_repository(tmp_path)
    package = fixtures.install_package(tmp_path)
    proposed = settings.settings_proposal(store, package, settings.changed_overrides(package))
    if change == "captured":
        proposed = proposed.model_copy(update={"candidate": fixtures.proposal(store, package).candidate})
    if change == "schema":
        proposed = settings.settings_proposal(store, package, settings.invalid_overrides(package))
    if change == "owner":
        changed = proposed.settings_changes[0].model_copy(update={"extension_id": "other.owner"})
        proposed = proposed.model_copy(update={"settings_changes": (changed,)})
    with pytest.raises(ValueError, match=r"settings|runtime|valid|schema"):
        store.accept_extension_operation(proposed, fixtures.NOW)
    assert not store.read_extension_lifecycle().settings
