# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify real manager admission, ownership, stored outcomes, and restart selection."""

from contextlib import closing
from pathlib import Path

import pytest

from extensions.manager_contract import ManagerStateError
from extensions.runtime_ownership_contract import RuntimeBusyError
from tests.extension_host import manager_assertions as checks, manager_fixture as fixtures, package_fixture

PACKAGES = "packages"
ENABLE = "enable"
ACCEPTED = "accepted"


def test_initial_restore_and_exclusive_owner(tmp_path: Path) -> None:
    """An empty installation still has a prepared runtime and one exclusive manager."""
    with closing(fixtures.open_manager(tmp_path)) as host:
        host.finish()
        state = host.controller.read_state()
        assert state.active_runtime == checks.committed(state).runtime_revision
        with pytest.raises(RuntimeBusyError):
            fixtures.open_manager(tmp_path, drain_seconds=0.1)
        assert host.controller.read_state().lifecycle.manager_id == state.lifecycle.manager_id
    with closing(fixtures.open_manager(tmp_path)) as replacement:
        replacement.finish()
        assert replacement.controller.read_state().active_runtime != state.active_runtime


def test_manager_enables_and_disables_web_package(tmp_path: Path) -> None:
    """Only a ready engine-boundary commit changes the enabled set."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with closing(fixtures.open_manager(tmp_path)) as host:
        host.finish()
        request = host.proposal(ENABLE)
        assert host.controller.submit_operation(request).status == ACCEPTED
        assert not checks.committed(host.controller.read_state()).packages
        host.finish()
        checks.require_operation(host, ENABLE)
        request = host.proposal("disable", ())
        assert host.controller.submit_operation(request).status == ACCEPTED
        host.finish()
        checks.require_disabled_directory(host)


def test_close_preserves_committed_state(tmp_path: Path) -> None:
    """Shutdown removes live resources but does not save an empty replacement set."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with closing(fixtures.open_manager(tmp_path)) as host:
        host.finish()
        assert host.controller.submit_operation(host.proposal(ENABLE)).status == ACCEPTED
        host.finish()
        active = host.controller.read_state()
    with closing(fixtures.open_manager(tmp_path)) as restarted:
        restarted.finish()
        state = restarted.controller.read_state()
        assert state.active_runtime != active.active_runtime
        assert checks.committed(state).packages == checks.committed(active).packages
        assert state.lifecycle.manager_id != active.lifecycle.manager_id


def test_manager_replay_does_not_prepare_twice(tmp_path: Path) -> None:
    """Exact retry keeps its operation and runtime reservation before and after commit."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with closing(fixtures.open_manager(tmp_path)) as host:
        host.finish()
        request = host.proposal(ENABLE)
        accepted = host.controller.submit_operation(request)
        assert accepted.status == ACCEPTED
        assert host.controller.submit_operation(request).operation == accepted.operation
        host.finish()
        assert host.controller.submit_operation(request).status == "replayed"
        assert host.controller.publish_ready().status == "idle"


def test_closed_manager_rejects_new_requests(tmp_path: Path) -> None:
    """An old controller cannot affect the next native owner of the data directory."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    host = fixtures.open_manager(tmp_path, drain_seconds=0.1)
    host.finish()
    request = host.proposal(ENABLE)
    host.close()
    host.close()
    with closing(fixtures.open_manager(tmp_path)) as replacement:
        with pytest.raises(ManagerStateError):
            host.controller.submit_operation(request)
        replacement.finish()
        assert host.controller.read_state().phase == "closed"
