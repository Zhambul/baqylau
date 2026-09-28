# Copyright (c) 2026 Zhambyl Yermagambet
"""Disable an extension that failed too many consecutive worker calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import uuid4

from audit.failures import CoalescingFailureRecorder, FailureContext
from extensions.lifecycle_control_contract import ExtensionLifecycleControl

if TYPE_CHECKING:
    from extensions.models.extension_health import ExtensionHealth


@dataclass(frozen=True)
class FailureDisable:
    """Submit one recorded failure operation; a refused change goes to the audit and never stops the engine."""

    control: ExtensionLifecycleControl
    failures: CoalescingFailureRecorder

    def __call__(self, health: ExtensionHealth) -> None:
        """Disable the failed extension and its required dependents."""
        try:
            self.control.disable_failed(health.extension_id, f"failure-{uuid4().hex}")
        except Exception:  # noqa: BLE001 -- A refused disable must not stop the engine; the audit keeps it.
            self.failures.record("extension failure disable", FailureContext(source=health.extension_id))
