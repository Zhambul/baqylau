# Copyright (c) 2026 Zhambyl Yermagambet
"""Compose scoped source processing without starting workers during request-only setup."""

from functools import partial
from typing import Annotated

from fastapi import Depends

from app import (
    provider_audit_storage,
    provider_extension_health,
    provider_extension_policy,
    provider_extension_registry,
    provider_extension_runtime,
    provider_fact_storage,
    provider_session_storage,
    provider_work_queue,
)
from app.injection import singleton
from audit.failures import CoalescingFailureRecorder
from core.work_queue import WorkKind
from extensions import (
    processing_contract,
    processing_runtime,
    source_resources,
    source_scope_contract,
    source_scopes,
)
from extensions.interpretation_resources import InterpretationStores
from repository.contract.interpretations import InterpretationRepository
from repository.contract.observations import ObservationRepository
from repository.contract.source_reads import ExtensionSourceRepository


@singleton
def extension_scope_registry(queue: provider_work_queue.EngineWork) -> source_scope_contract.ExtensionScopeRegistry:
    """Let host views and jobs retain repository scopes independently of sessions.

    Returns:
        The process-local scope owner shared by the engine and host consumers.

    """
    return source_scopes.ActiveExtensionScopes(partial(queue.put, WorkKind.SOURCES))


ScopeRegistry = Annotated[source_scope_contract.ExtensionScopeRegistry, Depends(extension_scope_registry)]


@singleton
def extension_source_scopes(
    explicit: ScopeRegistry, sessions: provider_session_storage.SessionDataStore,
) -> source_scope_contract.ExtensionSourceScopes:
    """Combine explicit host scopes with every currently running core actor.

    Returns:
        The complete data-only source scope selector.

    """
    return source_scopes.SessionSourceScopes(sessions, explicit)


SourceScopes = Annotated[source_scope_contract.ExtensionSourceScopes, Depends(extension_source_scopes)]
SourceStore = Annotated[ExtensionSourceRepository, Depends(provider_fact_storage.extension_sources)]


@singleton
def extension_source_services(
    runtime: provider_extension_runtime.Runtime, registry: provider_extension_registry.Registry,
    scopes: SourceScopes, repository: SourceStore, ledger: provider_extension_registry.CallLedger,
) -> source_resources.SourceServices | None:
    """Use the daemon's owned manager, not stored metadata that claims a worker is alive.

    Returns:
        Complete dependencies, or no source runtime in request-only applications.

    """
    if runtime.manager is None:
        return None
    return source_resources.SourceServices(runtime.manager, registry, scopes, repository, ledger)


SourceServices = Annotated[source_resources.SourceServices | None, Depends(extension_source_services)]


@singleton
def interpretation_stores(
    observations: Annotated[ObservationRepository, Depends(provider_fact_storage.observations)],
    facts: Annotated[InterpretationRepository, Depends(provider_fact_storage.interpretations)],
) -> InterpretationStores:
    """Share the complete mixed storage boundary with the engine.

    Returns:
        Original input and interpretation stores for one ordered writer.

    """
    return InterpretationStores(observations, facts)


@singleton
def source_callbacks(
    queue: provider_work_queue.EngineWork, audit: provider_audit_storage.Recorder,
    health: provider_extension_health.Health,
) -> source_resources.SourceCallbacks:
    """Notify the engine, write failures to the audit, and count them for health.

    Returns:
        The callbacks of source processing.

    """
    return source_resources.SourceCallbacks(
        partial(queue.put, WorkKind.SOURCES), CoalescingFailureRecorder(audit, "extension sources"), health=health,
    )


@singleton
def extension_source_processing(
    services: SourceServices,
    callbacks: Annotated[source_resources.SourceCallbacks, Depends(source_callbacks)],
    stores: Annotated[InterpretationStores, Depends(interpretation_stores)],
    source_policy: provider_extension_policy.SourceLimits,
) -> processing_contract.ExtensionProcessing | None:
    """Build the event-driven source coordinator without opening a worker.

    Returns:
        Processing which captures the active registry for each complete engine pass.

    """
    if services is None:
        return None
    return processing_runtime.ProcessingRuntime(services, callbacks, stores, source_policy)


Processing = Annotated[
    processing_contract.ExtensionProcessing | None, Depends(extension_source_processing),
]
