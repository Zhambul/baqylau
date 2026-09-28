# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the services that the daemon gives its job executor."""

from dataclasses import dataclass, field

from extensions.control_policy import ExtensionControlPolicy
from extensions.observer_models import ObserverStores
from extensions.registry_contract import ExtensionRegistry
from extensions.runtime_identity import ManagerStateReads
from extensions.source_scope_contract import ExtensionScopeRegistry


@dataclass(frozen=True)
class JobExecutorServices:
    """Keep the registry, the job stores, the manager, the write policy, and the source scopes together."""

    registry: ExtensionRegistry
    stores: ObserverStores
    manager: ManagerStateReads
    policy: ExtensionControlPolicy = field(default_factory=ExtensionControlPolicy)
    scopes: ExtensionScopeRegistry | None = None
