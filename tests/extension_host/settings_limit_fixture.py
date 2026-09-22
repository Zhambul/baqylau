# Copyright (c) 2026 Zhambyl Yermagambet
"""Seed the actual settings scope bound through the private manager."""

from baqylau_extension_api.models.scopes import WorkspaceScope

from extensions.models.registry import ScopedRuntimeSettings
from extensions.models.settings import SettingsOverrides
from tests.extension_host import (
    lifecycle_control_fixture as controls,
    lifecycle_settings_fixture,
    settings_control_fixture as settings,
)

SCOPE_LIMIT = 1000
FIRST_SCOPE = WorkspaceScope(workspace_id="scope-0")


def fill_scopes(case: controls.ControlHost) -> None:
    """Seed one full bound without sending 1,000 separate user requests."""
    settings.enable(case)
    committed = case.host.controller.read_state().lifecycle.committed_runtime
    assert committed is not None
    package = committed.packages[0]
    assert package.settings.default is not None
    overrides = SettingsOverrides(revision=1, scopes=tuple(
        ScopedRuntimeSettings(scope=WorkspaceScope(workspace_id=f"scope-{index}"), settings=package.settings.default)
        for index in range(SCOPE_LIMIT)
    ))
    proposal = lifecycle_settings_fixture.settings_proposal(case.host.runtime.store, package, overrides)
    assert case.host.controller.submit_operation(proposal).status == "accepted"
    case.host.finish()
