# Copyright (c) 2026 Zhambyl Yermagambet
"""Confirm complete required removal while preserving optional and unrelated peers."""

from contextlib import closing
from pathlib import Path
from typing import Final

import pytest

from extensions.lifecycle_control_contract import LifecycleConflictError, LifecycleRequestError
from extensions.models.lifecycle_requests import plan_request
from tests.extension_host import (
    control_assertions,
    lifecycle_control_fixture as controls,
    lifecycle_dependency_fixture as fixture,
)

ENABLE: Final = "enable"


def test_required_removal_needs_confirmation(tmp_path: Path) -> None:
    """Disable removes the transitive required set and keeps the optional consumer."""
    fixture.write_graph(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        for owner in (fixture.BASE, fixture.CHILD, fixture.LEAF, fixture.OPTIONAL):
            case.control.change_lifecycle(owner, case.request(ENABLE, owner, owner))
            case.host.finish()
        request = case.request("disable", "remove-base", fixture.BASE)
        preview = case.control.preview_lifecycle(fixture.BASE, plan_request(request))
        assert preview.affected_extensions == (fixture.LEAF, fixture.CHILD, fixture.BASE)
        with pytest.raises(LifecycleConflictError, match="exact required dependents"):
            case.control.change_lifecycle(fixture.BASE, request)
        request = request.model_copy(update={"confirmed_dependents": (fixture.CHILD, fixture.LEAF)})
        case.control.change_lifecycle(fixture.BASE, request)
        case.host.finish()
        control_assertions.require_enabled_owners(case, (fixture.OPTIONAL,))


def test_enable_does_not_silently_enable_provider(tmp_path: Path) -> None:
    """A new package cannot start other installed feature code without a user request."""
    fixture.write_graph(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        with pytest.raises(LifecycleRequestError, match="incompatible dependencies"):
            case.control.change_lifecycle(fixture.CHILD, case.request(ENABLE, "missing-provider", fixture.CHILD))
        assert case.host.controller.read_state().lifecycle.pending_operation is None


def test_removal_uses_retained_declarations(tmp_path: Path) -> None:
    """Removed or edited source packages cannot hide a required active dependent."""
    fixture.write_graph(tmp_path)
    with closing(controls.open_control(tmp_path)) as case:
        case.control.change_lifecycle(fixture.BASE, case.request(ENABLE, "base", fixture.BASE))
        case.host.finish()
        case.control.change_lifecycle(fixture.CHILD, case.request(ENABLE, "child", fixture.CHILD))
        case.host.finish()
        (tmp_path / "packages").rename(tmp_path / "removed-source")
        case.rescan()
        request = case.request("disable", "remove", fixture.BASE)
        preview = case.control.preview_lifecycle(fixture.BASE, plan_request(request))
        assert preview.affected_extensions == (fixture.CHILD, fixture.BASE)
