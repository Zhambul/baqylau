# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the services that the daemon gives its job executor."""

from dataclasses import dataclass, field

from audit.failures import CoalescingFailureRecorder
from extensions.control_policy import ExtensionControlPolicy
from extensions.observer_models import ObserverStores
from extensions.registry_contract import ExtensionRegistry
from extensions.runtime_identity import ManagerStateReads
from extensions.source_scope_contract import ExtensionScopeRegistry


@dataclass(frozen=True)
class JobExecutorServices:
    """Keep the registry, the job stores, the manager, the failure record, the write policy, and the source scopes."""

    registry: ExtensionRegistry
    stores: ObserverStores
    manager: ManagerStateReads
    failures: CoalescingFailureRecorder
    policy: ExtensionControlPolicy = field(default_factory=ExtensionControlPolicy)
    scopes: ExtensionScopeRegistry | None = None
