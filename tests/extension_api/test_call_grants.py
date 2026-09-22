# Copyright (c) 2026 Zhambyl Yermagambet
"""Check host call authority without trusting feature-supplied route or scope fields."""

from contextlib import ExitStack

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.scopes import InstallationScope, WorkspaceScope
from baqylau_extension_api.runtime import call_grants, grant_models
from baqylau_extension_api.runtime.host_context import host_call_scope

from tests.extension_api import service_samples as fixtures

ALPHA_ENV = fixtures.environment(fixtures.ALPHA)
BETA_ENV = fixtures.environment(fixtures.BETA)


def test_root_and_child_keep_scope_and_deadline() -> None:
    """Forwarding grants do not extend time or let a peer select a new scope."""
    calls = call_grants.HostCallLedger()
    with calls.root(ALPHA_ENV, InstallationScope(), 3) as root:
        with calls.forward(root, BETA_ENV) as child:
            assert (child.expires_at, child.scope) == (root.expires_at, root.scope)
            assert child.route == (fixtures.ALPHA, fixtures.BETA)
            assert child.parents == (root.call_id,)
            assert calls.require_call(BETA_ENV, InstallationScope()) == child
        assert calls.require_call(ALPHA_ENV, InstallationScope()) == root


@pytest.mark.parametrize("change", ["owner", "runtime", "scope"])
def test_host_grant_rejects_changed_identity(change: str) -> None:
    """A known token cannot authorize a different worker or selected scope."""
    calls = call_grants.HostCallLedger()
    environment = fixtures.environment(fixtures.BETA if change == "owner" else fixtures.ALPHA)
    scope = WorkspaceScope(workspace_id="other") if change == "scope" else InstallationScope()
    if change == "runtime":
        environment = environment.model_copy(update={"runtime_revision": "different"})
    with (
        calls.root(ALPHA_ENV, InstallationScope(), 3),
        pytest.raises(ExtensionContractError),
    ):
        calls.require_call(environment, scope)


def test_released_host_grant_cannot_be_reused() -> None:
    """An old correlation reference does not restore a completed host operation."""
    calls = call_grants.HostCallLedger()
    with calls.root(ALPHA_ENV, InstallationScope(), 3) as root:
        call_id = root.call_id
    with host_call_scope(call_id), pytest.raises(ExtensionContractError):
        calls.require_call(ALPHA_ENV, InstallationScope())


def test_revoked_parent_invalidates_child() -> None:
    """A nested callback cannot continue after its parent runtime is removed."""
    calls = call_grants.HostCallLedger()
    with (
        calls.root(ALPHA_ENV, InstallationScope(), 3) as root,
        calls.forward(root, BETA_ENV),
    ):
        calls.revoke_runtime(ALPHA_ENV)
        with pytest.raises(ExtensionContractError, match="parent was released"):
            calls.require_call(BETA_ENV, InstallationScope())


def test_expired_grant_cannot_be_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A later callback cannot get more time by starting a child call."""
    calls = call_grants.HostCallLedger()
    with calls.root(ALPHA_ENV, InstallationScope(), 3) as root:
        monkeypatch.setattr(call_grants, "monotonic", lambda: root.expires_at + 1)
        with pytest.raises(ExtensionContractError):
            calls.require_call(ALPHA_ENV, InstallationScope())
        with pytest.raises(ExtensionContractError), calls.forward(root, BETA_ENV):
            pytest.fail("An expired grant was forwarded.")


def test_host_call_route_has_a_depth_bound() -> None:
    """Acyclic service calls still have a fixed maximum depth."""
    calls = call_grants.HostCallLedger()
    with ExitStack() as stack:
        parent = stack.enter_context(calls.root(ALPHA_ENV, InstallationScope(), 3))
        for index in range(grant_models.MAX_CALL_DEPTH - 1):
            parent = stack.enter_context(calls.forward(parent, fixtures.environment(f"test.peer{index}")))
        with pytest.raises(ExtensionContractError, match="depth limit"):
            stack.enter_context(calls.forward(parent, fixtures.environment("test.last")))


def test_host_call_count_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected child does not consume its parent's grant or leak another grant."""
    calls = call_grants.HostCallLedger()
    monkeypatch.setattr(call_grants, "MAX_ACTIVE_HOST_CALLS", 1)
    with calls.root(ALPHA_ENV, InstallationScope(), 3) as root:
        with (
            pytest.raises(ExtensionContractError, match="limit reached"),
            calls.forward(root, BETA_ENV),
        ):
            pytest.fail("A full call store accepted another grant.")
        assert calls.require_call(ALPHA_ENV, InstallationScope()) == root
