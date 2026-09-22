# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep inheritance and explicit path selection separate from migration execution."""

from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from extensions.lifecycle_control_contract import LifecycleRequestError
from tests.extension_host import (
    lifecycle_control_fixture as controls,
    migration_host_fixture as fixture,
    package_fixture,
)

RELOAD: Final = "reload"
OLD_VALUE = '{"label":"Chosen"}'


def test_no_override_uses_new_default(tmp_path: Path, runtime_wheels: Path) -> None:
    """Inherited values select the new default without running a conversion."""
    source = fixture.write_package(tmp_path, runtime_wheels)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.activate(case)
        fixture.select_version(source, 2)
        case.rescan()
        fixture.activate(case, RELOAD)
        current = fixture.read(case)
        assert current.override is None
        assert current.settings_revision == 0
        assert current.effective.json_text == '{"title":"Default"}'
        assert not (tmp_path / fixture.MIGRATING).exists()


def test_declared_reverse_conversion(tmp_path: Path, runtime_wheels: Path) -> None:
    """A reverse conversion works only through its separately declared path."""
    source = fixture.write_package(tmp_path, runtime_wheels)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.activate(case)
        fixture.save(case, OLD_VALUE)
        fixture.select_version(source, 2)
        case.rescan()
        fixture.activate(case, RELOAD)
        fixture.select_version(source, 1)
        case.rescan()
        fixture.activate(case, RELOAD)
        assert fixture.read(case).effective.json_text == OLD_VALUE
        assert fixture.read(case).effective == fixture.activated(tmp_path).settings


def test_missing_path_rejected_before_admission(tmp_path: Path, runtime_wheels: Path) -> None:
    """A valid package with only record paths cannot migrate settings implicitly."""
    source = fixture.write_package(tmp_path, runtime_wheels)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.activate(case)
        fixture.save(case, OLD_VALUE)
        fixture.select_version(source, 2)
        _remove_settings_path(source)
        case.rescan()
        request = case.request(RELOAD, "missing-path", fixture.OWNER)
        with pytest.raises(LifecycleRequestError, match="incompatible"):
            case.control.change_lifecycle(fixture.OWNER, request)
        assert not (tmp_path / fixture.MIGRATING).exists()
        assert fixture.read(case).effective.json_text == OLD_VALUE


def test_conversion_waits_for_publication(tmp_path: Path, runtime_wheels: Path) -> None:
    """A busy registry keeps converted values outside accepted settings reads."""
    source = fixture.write_package(tmp_path, runtime_wheels)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.activate(case)
        fixture.save(case, OLD_VALUE)
        fixture.select_version(source, 2)
        case.rescan()
        request = case.request(RELOAD, "held-migration", fixture.OWNER)
        case.control.change_lifecycle(fixture.OWNER, request)
        case.host.wait_ready()
        registry = case.host.runtime.preparation.registry
        with registry.read_snapshot():
            assert case.host.controller.publish_ready().status == "busy"
            assert fixture.read(case).effective.json_text == OLD_VALUE
        case.host.finish()
        assert fixture.read(case).effective.json_text == '{"title":"Chosen"}'


def _remove_settings_path(source: Path) -> None:
    manifest = package_fixture.read_manifest(source)
    package_fixture.save_manifest(source, manifest.model_copy(update={
        "migration_paths": tuple(path for path in manifest.migration_paths if path.kind == "records"),
    }))
