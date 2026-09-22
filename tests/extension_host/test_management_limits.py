# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject complete candidate limits without leaking values or changing accepted state."""

from contextlib import closing
from pathlib import Path

import pytest

from extensions import lifecycle_request_identity
from extensions.lifecycle_control_contract import LifecycleRequestError
from extensions.models import lifecycle_operations
from tests.extension_host import (
    lifecycle_control_fixture as controls,
    lifecycle_dependency_fixture as packages,
    settings_control_fixture as settings,
    settings_limit_fixture as limits,
)


def test_scope_limit_keeps_accepted_state(tmp_path: Path) -> None:
    """The 1,001st explicit scope is a request error, not an internal failure."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        limits.fill_scopes(case)
        request = settings.request(case, "too-many-scopes", '"private setting"', settings.WORKSPACE)
        previous = case.host.controller.read_state().lifecycle
        with pytest.raises(LifecycleRequestError, match="host limit") as failed:
            settings.service(case).change_settings(settings.OWNER, request)
        assert "private setting" not in str(failed.value)
        assert case.host.controller.read_state().lifecycle == previous
        assert case.host.controller.read_operation(lifecycle_request_identity.request_operation_id(
            request.request_id,
        )) is None


@pytest.mark.parametrize("document", ['"replacement"', None])
def test_full_scope_set_allows_edit_and_reset(tmp_path: Path, document: str | None) -> None:
    """A full scope set must still permit replacement and removal."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        limits.fill_scopes(case)
        request = settings.save(case, "change-at-limit", document, limits.FIRST_SCOPE)
        current = settings.snapshot(case, limits.FIRST_SCOPE)
        assert current.settings_revision == request.expected_settings_revision + 1
        assert current.override == request.document


def test_settings_candidate_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the actual model byte check with a small test bound."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        request = settings.request(case, "large-settings", '"private setting"')
        previous = case.host.controller.read_state().lifecycle
        monkeypatch.setattr(lifecycle_operations, "MAX_OPERATION_BYTES", 1)
        with pytest.raises(LifecycleRequestError, match="host limit"):
            settings.service(case).change_settings(settings.OWNER, request)
        assert case.host.controller.read_state().lifecycle == previous


def test_lifecycle_candidate_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Lifecycle and settings changes share the same checked preparation boundary."""
    packages.write_settings_package(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        request = case.request("enable", "large-lifecycle")
        previous = case.host.controller.read_state().lifecycle
        monkeypatch.setattr(lifecycle_operations, "MAX_OPERATION_BYTES", 1)
        with pytest.raises(LifecycleRequestError, match="host limit"):
            case.control.change_lifecycle(settings.OWNER, request)
        assert case.host.controller.read_state().lifecycle == previous
