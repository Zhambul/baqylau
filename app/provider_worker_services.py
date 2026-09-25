# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide the host services that extension workers call: peer jobs, record migrations, and secret values."""

from typing import Annotated

from fastapi import Depends

from app import (
    provider_audit_storage,
    provider_databases,
    provider_extension_executor,
    provider_extension_jobs,
    provider_extension_policy,
    provider_inference,
)
from app.injection import singleton
from extensions.impl.keychain_secrets import KeychainSecretStore
from extensions.peer_jobs import PeerJobs
from extensions.preparation_runner import BoundedPreparationRunner
from extensions.preparation_services import PreparationServices, ReportingStores
from extensions.secret_store_contract import SecretStore
from repository.impl.sqlite import (
    extension_lifecycle,
    extension_records,
    observations,
    record_migrations,
    scope_relations,
    session_rows,
)


@singleton
def extension_secrets() -> SecretStore:
    """Return the macOS Keychain store of extension secret values.

    Returns:
        The secret store; no value is read during construction.

    """
    return KeychainSecretStore()


Secrets = Annotated[SecretStore, Depends(extension_secrets)]


@singleton
def extension_peer_jobs(
    jobs: provider_extension_jobs.Jobs,
    policy: provider_extension_policy.ControlPolicy,
    scheduler: provider_extension_executor.JobSchedulerDep,
) -> PeerJobs:
    """Let peer commands use the job store, the write policy, and the scheduling slot.

    Returns:
        The peer job host for worker service callbacks.

    """
    return PeerJobs(jobs=jobs, policy=policy, scheduler=scheduler)


@singleton
def preparation_services(
    database: provider_databases.MainDb,
    peer_jobs: Annotated[PeerJobs, Depends(extension_peer_jobs)],
    secrets: Secrets,
    models: provider_inference.InferenceModels,
    audit: provider_audit_storage.AuditWrites,
) -> PreparationServices:
    """Group the host services that candidate workers can call.

    Returns:
        Peer jobs, the record migration store, and the secret store.

    """
    stores = ReportingStores(
        observations.SqliteObservationRepository(database),
        extension_lifecycle.SqliteExtensionLifecycleRepository(database),
        audit,
    )
    return PreparationServices(
        peer_jobs,
        record_migrations.SqliteRecordMigrationStore(database),
        secrets,
        scope_relations.SqliteScopeRelations(database),
        BoundedPreparationRunner(),
        models,
        extension_records.SqliteExtensionRecordRepository(database),
        stores,
        session_rows.SqliteSessionRows(database),
    )


WorkerServices = Annotated[PreparationServices, Depends(preparation_services)]
