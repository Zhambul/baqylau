# Copyright (c) 2026 Zhambyl Yermagambet
"""Admit checked user lifecycle requests without exposing runtime authority."""

from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from extensions.lifecycle_control_contract import LifecycleConflictError, LifecycleRequestError
from extensions.models.lifecycle_requests import plan_request
from tests.extension_host import control_assertions, lifecycle_control_fixture as fixtures, package_fixture

OWNER = package_fixture.OWNER
ENABLE: Final = "enable"
PACKAGES = "packages"


def test_preview_does_not_admit_or_prepare(tmp_path: Path) -> None:
    """Preview returns affected owners and leaves both runtime and stored state unchanged."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with closing(fixtures.open_control(tmp_path)) as case:
        before = case.host.controller.read_state()
        request = case.request(ENABLE, "preview")
        preview = case.control.preview_lifecycle(OWNER, plan_request(request))
        assert preview.affected_extensions == (OWNER,)
        assert case.host.controller.read_state() == before
        assert case.host.controller.publish_ready().status == "idle"


def test_enable_and_disable_through_control(tmp_path: Path) -> None:
    """Only the host selects operation identity, runtime identity, and the full set."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with closing(fixtures.open_control(tmp_path)) as case:
        admitted = case.control.change_lifecycle(OWNER, case.request(ENABLE, "first"))
        assert admitted.status == "accepted" and admitted.operation is not None
        assert admitted.operation.proposal.request_origin is not None
        case.host.finish()
        assert case.control.change_lifecycle(OWNER, case.request("disable", "second")).status == "accepted"
        case.host.finish()
        control_assertions.require_empty(case)


def test_reload_selects_new_captured_digest(tmp_path: Path) -> None:
    """A new selected digest replaces only the target; source changes alone do not enable it."""
    source = package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with closing(fixtures.open_control(tmp_path)) as case:
        request = case.request(ENABLE, "initial")
        case.control.change_lifecycle(OWNER, request)
        case.host.finish()
        package_fixture.write_file(source, "added.txt", b"new package content")
        case.rescan()
        request = case.request("reload", "updated")
        assert case.control.change_lifecycle(OWNER, request).status == "accepted"
        case.host.finish()
        control_assertions.require_digest(case, request.package_digest)


@pytest.mark.parametrize("action", [ENABLE, "reload", "disable"])
def test_invalid_target_state_is_rejected(tmp_path: Path, action: str) -> None:
    """Enable and reload remain distinct operations; absent disable is not invented work."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with closing(fixtures.open_control(tmp_path)) as case:
        request = case.request(ENABLE, "initial")
        if action == ENABLE:
            case.control.change_lifecycle(OWNER, request)
            case.host.finish()
        changed = case.request(ENABLE, "invalid").model_copy(update={
            "action": action, "package_digest": None if action == "disable" else request.package_digest,
        })
        with pytest.raises(LifecycleRequestError):
            case.control.change_lifecycle(OWNER, changed)


def test_unconfirmed_extra_owner_is_rejected(tmp_path: Path) -> None:
    """The confirmation field cannot add an unrelated package to the planned change."""
    package_fixture.write_package(tmp_path / PACKAGES, web=True)
    with closing(fixtures.open_control(tmp_path)) as case:
        request = case.request(ENABLE, "extra").model_copy(update={"confirmed_dependents": ("test.unrelated",)})
        with pytest.raises(LifecycleConflictError, match="exact required dependents"):
            case.control.change_lifecycle(OWNER, request)
        assert case.host.controller.read_state().lifecycle.pending_operation is None
