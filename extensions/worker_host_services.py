# Copyright (c) 2026 Zhambyl Yermagambet
"""Assemble the host services that one candidate worker declares."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.runtime.call_grants import HostCallLedger

from extensions import (
    audit_access,
    inference_access,
    observation_sink,
    process_access,
    record_access,
    secret_access,
    session_access,
)
from extensions.preparation_services import PreparationServices
from extensions.registry_contract import ExtensionRegistry
from extensions.registry_services import RegistryDirectory, RegistryServiceAccess

if TYPE_CHECKING:
    from baqylau_extension_api.contracts import credentials, processes, record_reads, reporting, session_lists
    from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest


@dataclass(frozen=True)
class WorkerHostServices:
    """Keep the registry, the call ledger, and the host services for every candidate worker."""

    registry: ExtensionRegistry
    ledger: HostCallLedger
    services: PreparationServices

    def for_worker(self, request: WorkerLoadRequest) -> ExtensionHostServices:
        """Give one worker only the services that its manifest declares.

        Returns:
            The directory, peer access, and each declared host service.

        """
        return ExtensionHostServices(
            RegistryDirectory(self.registry), request.environment,
            RegistryServiceAccess(request, self.registry, self.ledger, self.services.peer_jobs),
            self._credentials(request),
            self._processes(request),
            self._inference(request),
            self._records(request),
            *self._reporting(request),
            self._sessions(request),
        )

    def _credentials(self, request: WorkerLoadRequest) -> credentials.ExtensionCredentialService | None:
        secrets = self.services.secrets
        if secrets is None or not secret_access.declared_secrets(request.manifest):
            return None
        return secret_access.HostCredentialService(request.manifest, secrets)

    def _processes(self, request: WorkerLoadRequest) -> processes.ExtensionProcessService | None:
        runner = self.services.process_runner
        if runner is None or not request.manifest.contributions.processes:
            return None
        return process_access.HostProcessService(request.manifest, runner)

    def _inference(self, request: WorkerLoadRequest) -> processes.ExtensionInferenceService | None:
        models = self.services.models
        if models is None or not request.manifest.contributions.uses_inference:
            return None
        return inference_access.HostInferenceService(models, request.manifest.extension_id)

    def _records(self, request: WorkerLoadRequest) -> record_reads.ExtensionRecordReader | None:
        store = self.services.records
        if store is None or not request.manifest.contributions.collections:
            return None
        return record_access.HostRecordReader(request.manifest, store)

    def _sessions(self, request: WorkerLoadRequest) -> session_lists.ExtensionSessionDirectory | None:
        rows = self.services.sessions
        if rows is None or not request.manifest.contributions.uses_sessions:
            return None
        return session_access.HostSessionDirectory(rows)

    def _reporting(
        self, request: WorkerLoadRequest,
    ) -> tuple[reporting.ExtensionObservationSink | None, reporting.ExtensionAuditService | None]:
        stores = self.services.reporting
        if stores is None:
            return None, None
        sink = None
        if request.manifest.contributions.source_types:
            sink = observation_sink.HostObservationSink(request.environment, stores.observations, stores.lifecycle)
        return sink, audit_access.HostAuditService(request.manifest.extension_id, stores.audit)
