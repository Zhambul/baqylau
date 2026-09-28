# Copyright (c) 2026 Zhambyl Yermagambet
"""Group the host services that runtime preparation gives to candidate workers."""

from dataclasses import dataclass

from extensions.environment_contract import PreparationRunner
from extensions.models.scope_relations import ScopeRelations
from extensions.peer_jobs import PeerJobs
from extensions.secret_store_contract import SecretStore
from inference.contract import ModelFactory
from repository.contract.audit import AuditWriteRepository
from repository.contract.extension_lifecycle import ExtensionLifecycleRepository
from repository.contract.extension_records import ExtensionRecordRepository
from repository.contract.observations import ObservationRepository
from repository.contract.record_migrations import RecordMigrationStore
from repository.contract.session_rows import SessionRows


@dataclass(frozen=True)
class ReportingStores:
    """Keep the stores behind the observation sink and the audit service."""

    observations: ObservationRepository
    lifecycle: ExtensionLifecycleRepository
    audit: AuditWriteRepository


@dataclass(frozen=True)
class PreparationServices:
    """Keep the services that candidate workers can call; each can be absent in a test host."""

    peer_jobs: PeerJobs | None = None
    record_migrations: RecordMigrationStore | None = None
    secrets: SecretStore | None = None
    scope_relations: ScopeRelations | None = None
    process_runner: PreparationRunner | None = None
    models: ModelFactory | None = None
    records: ExtensionRecordRepository | None = None
    reporting: ReportingStores | None = None
    sessions: SessionRows | None = None
