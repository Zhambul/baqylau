# Copyright (c) 2026 Zhambyl Yermagambet
"""Inspect real registry state after completed user lifecycle requests."""

from tests.extension_host.lifecycle_control_fixture import ControlHost


def require_empty(case: ControlHost) -> None:
    """Require no enabled package after the completed removal."""
    registry = case.host.runtime.preparation.registry
    with registry.read_snapshot() as selected:
        assert not selected.snapshot.active_order


def require_digest(case: ControlHost, digest: str | None) -> None:
    """Check the selected active bytes, not only the accepted operation response."""
    registry = case.host.runtime.preparation.registry
    with registry.read_snapshot() as selected:
        package = selected.snapshot.packages[0]
        identity = package.entry.extension_info
        assert identity.package_digest == digest


def require_enabled_owners(case: ControlHost, expected: tuple[str, ...]) -> None:
    """Read the active directory after a confirmed dependent removal."""
    selected = case.host.controller.read_state().directory
    assert selected is not None
    enabled = tuple(
        entry.extension_info.extension_id
        for entry in selected.entries
        if entry.state == "enabled"
    )
    assert enabled == expected
