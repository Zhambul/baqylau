# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject stale discovery selections before recording requested runtime changes."""

from contextlib import closing
from pathlib import Path

import pytest

from extensions.lifecycle_control_contract import LifecycleConflictError, LifecycleRequestError
from tests.extension_api import manifest_samples, samples
from tests.extension_host import lifecycle_control_fixture as fixture, package_fixture

OWNER = package_fixture.OWNER


def test_changed_catalog_rejects_old_selection(tmp_path: Path) -> None:
    """A previously selected package cannot bypass the current catalog revision."""
    source = package_fixture.write_package(tmp_path / "packages", web=True)
    with closing(fixture.open_control(tmp_path)) as case:
        request = case.request("enable", "old-catalog")
        package_fixture.write_file(source, "changed.txt", b"changed catalog bytes")
        case.rescan()
        with pytest.raises(LifecycleConflictError, match="catalog revision changed"):
            case.control.change_lifecycle(OWNER, request)
        assert case.host.controller.read_state().lifecycle.pending_operation is None


def test_wrong_digest_rejects_selected_package(tmp_path: Path) -> None:
    """Current revisions do not authorize different package bytes."""
    package_fixture.write_package(tmp_path / "packages", web=True)
    with closing(fixture.open_control(tmp_path)) as case:
        request = case.request("enable", "wrong-digest").model_copy(update={"package_digest": samples.DIGEST})
        with pytest.raises(LifecycleConflictError, match="bytes differ"):
            case.control.change_lifecycle(OWNER, request)
        assert not case.host.controller.read_state().lifecycle.intents


def test_invalid_package_rejects_admission(tmp_path: Path) -> None:
    """A missing declared asset stays visible in discovery but cannot be enabled."""
    source = package_fixture.write_package(tmp_path / "packages", web=True)
    with closing(fixture.open_control(tmp_path)) as case:
        request = case.request("enable", "invalid-package")
        (source / manifest_samples.MODULE_PATH).rename(source / "removed-module.js")
        case.rescan()
        catalog = case.control.catalog.read_extension_catalog()
        request = request.model_copy(update={"expected_catalog_revision": catalog.revision})
        with pytest.raises(LifecycleRequestError, match="one valid discovered package"):
            case.control.change_lifecycle(OWNER, request)
        assert not case.host.controller.read_state().lifecycle.intents
