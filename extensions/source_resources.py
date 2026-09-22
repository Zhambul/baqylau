# Copyright (c) 2026 Zhambyl Yermagambet
"""Group source dependencies without exposing repository connections to feature code."""

from collections.abc import Callable
from dataclasses import dataclass, field
from time import time

from baqylau_extension_api.runtime.call_grants import HostCallLedger

from audit.failures import CoalescingFailureRecorder
from extensions.manager_contract import ExtensionManager
from extensions.models.source_processing import SourceScopeKey, SourceScopePlan
from extensions.registry_contract import ExtensionRegistry
from extensions.source_scope_contract import ExtensionSourceScopes
from repository.contract.source_reads import ExtensionSourceRepository


@dataclass(frozen=True)
class SourceServices:
    """Use only explicit host protocols around the source worker boundary."""

    manager: ExtensionManager
    registry: ExtensionRegistry
    scopes: ExtensionSourceScopes
    repository: ExtensionSourceRepository
    ledger: HostCallLedger


@dataclass(frozen=True)
class SourceCallbacks:
    """Supply the application clock, work notices, and existing coalesced audit."""

    changed: Callable[[], None]
    failures: CoalescingFailureRecorder
    clock: Callable[[], float] = time


@dataclass
class SourcePlans:
    """Own data-only scheduling state on the engine thread, with no borrowed worker."""

    runtime_revision: str | None = None
    scopes: dict[SourceScopeKey, SourceScopePlan] = field(default_factory=dict)

    def select_runtime(self, runtime_revision: str) -> None:
        """Discard old scheduling data after the manager replaces its worker resources."""
        if runtime_revision != self.runtime_revision:
            self.runtime_revision = runtime_revision
            self.scopes.clear()
