# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify real worker conversion, atomic publication, failure retention, and restart."""

import os
from contextlib import closing
from pathlib import Path

import pytest
from baqylau_extension_api.models.scopes import WorkspaceScope

from tests import terminal_pty_waits
from tests.extension_host import lifecycle_control_fixture as controls, migration_host_fixture as fixture


def test_upgrade_migrates_before_activation(tmp_path: Path, runtime_wheels: Path) -> None:
    """Raw overrides and effective activation values change together after conversion."""
    source = fixture.write_package(tmp_path, runtime_wheels)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.activate(case)
        fixture.save(case, '{"label":"Chosen"}')
        fixture.select_version(source, 2)
        case.rescan()
        fixture.activate(case, "reload")
        assert fixture.read(case).override == fixture.activated(tmp_path).settings
        assert fixture.read(case).effective.json_text == '{"title":"Chosen"}'
        assert fixture.read(case).settings_revision == fixture.activated(tmp_path).settings_revision
    with closing(controls.open_control(tmp_path)) as restarted:
        assert fixture.read(restarted).effective == fixture.activated(tmp_path).settings
        assert fixture.read(restarted).effective.json_text == '{"title":"Chosen"}'


def test_workspace_migration_keeps_inheritance(tmp_path: Path, runtime_wheels: Path) -> None:
    """No installation override is created from an inherited default."""
    source = fixture.write_package(tmp_path, runtime_wheels)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.activate(case)
        fixture.save(case, '{"label":"Scoped"}', WorkspaceScope(workspace_id="selected"))
        fixture.select_version(source, 2)
        case.rescan()
        fixture.activate(case, "reload")
        assert fixture.read(case).override is None
        assert fixture.read(case).effective.json_text == '{"title":"Default"}'
        assert fixture.read(case, WorkspaceScope(workspace_id="selected")).effective.json_text == '{"title":"Scoped"}'


@pytest.mark.parametrize("label", ["reject", "invalid", "host_call"])
def test_bad_conversion_keeps_old_worker(tmp_path: Path, runtime_wheels: Path, label: str) -> None:
    """Explicit failure, invalid output, and a forbidden callback cannot publish."""
    source = fixture.write_package(tmp_path, runtime_wheels)
    with closing(controls.open_control(tmp_path)) as case:
        fixture.activate(case)
        fixture.save(case, f'{{"label":"{label}"}}')
        previous = int((tmp_path / fixture.ACTIVE).read_text(encoding="utf-8"))
        fixture.select_version(source, 2)
        case.rescan()
        request = case.request("reload", "failed-conversion", fixture.OWNER)
        assert case.control.change_lifecycle(fixture.OWNER, request).status == "accepted"
        case.host.finish("failed")
        os.kill(previous, 0)
        assert int((tmp_path / fixture.ACTIVE).read_text(encoding="utf-8")) == previous
        assert fixture.read(case).effective == fixture.activated(tmp_path).settings
        terminal_pty_waits.wait_for_process_exit(int((tmp_path / fixture.MIGRATING).read_text(encoding="utf-8")))
