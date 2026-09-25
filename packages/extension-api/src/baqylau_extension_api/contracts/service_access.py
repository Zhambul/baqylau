# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve, read, and submit work to declared peer services through the host, not private imports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from baqylau_extension_api.models.service_jobs import (
        ServiceCommandRequest,
        ServiceJobCancelRequest,
        ServiceJobCancelResult,
        ServiceJobRequest,
        ServiceJobResult,
    )
    from baqylau_extension_api.models.services import (
        ServiceQueryRequest,
        ServiceQueryResult,
        ServiceResolveRequest,
        ServiceResolveResult,
    )


@runtime_checkable
class ExtensionServiceAccess(Protocol):
    """Read versioned public services and submit public commands as durable host jobs."""

    def resolve_service(self, service_request: ServiceResolveRequest) -> ServiceResolveResult:
        """Resolve a declared peer service without executing its feature code."""

    def query_service(self, service_query: ServiceQueryRequest) -> ServiceQueryResult:
        """Read a public query through the current host-authorized call chain."""

    def submit_service_command(self, service_command: ServiceCommandRequest) -> ServiceJobResult:
        """Ask the host to accept a public peer command; the host returns a job reference."""

    def read_service_job(self, service_job: ServiceJobRequest) -> ServiceJobResult:
        """Read a peer job that this caller submitted."""

    def cancel_service_job(self, service_job_cancel: ServiceJobCancelRequest) -> ServiceJobCancelResult:
        """Ask the owning peer to stop an attempt that this caller submitted."""
