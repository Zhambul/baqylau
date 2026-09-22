# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve exact scope overrides without merging or disclosing other scopes."""

from contextlib import closing
from pathlib import Path

from tests.extension_host import (
    lifecycle_control_fixture as controls,
    lifecycle_dependency_fixture as packages,
    settings_control_fixture as fixture,
)

INSTALLATION_VALUE = '"installation value"'
WORKSPACE_VALUE = '"workspace value"'
OTHER_VALUE = '"other workspace value"'


def test_workspace_override_beats_installation(tmp_path: Path) -> None:
    """An exact workspace wins; another workspace keeps the installation fallback."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.save(case, "installation", INSTALLATION_VALUE)
        fixture.save(case, "workspace", WORKSPACE_VALUE, fixture.WORKSPACE)
        assert fixture.snapshot(case, fixture.WORKSPACE).effective.json_text == WORKSPACE_VALUE
        current = fixture.snapshot(case, fixture.OTHER_WORKSPACE)
        assert current.effective.json_text == INSTALLATION_VALUE
        assert current.override is None
        assert WORKSPACE_VALUE.strip('"') not in current.model_dump_json()


def test_workspace_reset_keeps_other_overrides(tmp_path: Path) -> None:
    """Reset removes one exact workspace without changing another workspace."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.save(case, "installation", INSTALLATION_VALUE)
        fixture.save(case, "workspace", WORKSPACE_VALUE, fixture.WORKSPACE)
        fixture.save(case, "other", OTHER_VALUE, fixture.OTHER_WORKSPACE)
        fixture.save(case, "reset-one", None, fixture.WORKSPACE)
        assert fixture.snapshot(case, fixture.WORKSPACE).effective.json_text == INSTALLATION_VALUE
        assert fixture.snapshot(case, fixture.OTHER_WORKSPACE).effective.json_text == OTHER_VALUE
        assert fixture.snapshot(case).effective.json_text == INSTALLATION_VALUE


def test_installation_reset_keeps_workspace(tmp_path: Path) -> None:
    """Removing fallback leaves an exact workspace choice intact."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.save(case, "installation", INSTALLATION_VALUE)
        fixture.save(case, "workspace", WORKSPACE_VALUE, fixture.WORKSPACE)
        fixture.save(case, "reset-fallback", None)
        current = fixture.snapshot(case, fixture.OTHER_WORKSPACE)
        assert current.override is None and current.effective == current.definition.defaults
        assert fixture.snapshot(case, fixture.WORKSPACE).effective.json_text == WORKSPACE_VALUE
