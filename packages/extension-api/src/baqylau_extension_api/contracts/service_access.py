# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve and read declared peer services through the host, not private imports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from baqylau_extension_api.models.services import (
        ServiceQueryRequest,
        ServiceQueryResult,
        ServiceResolveRequest,
        ServiceResolveResult,
    )


@runtime_checkable
class ExtensionServiceAccess(Protocol):
    """Read versioned public services; peer command acceptance is still pending."""

    def resolve_service(self, service_request: ServiceResolveRequest) -> ServiceResolveResult:
        """Resolve a declared peer service without executing its feature code."""

    def query_service(self, service_query: ServiceQueryRequest) -> ServiceQueryResult:
        """Read a public query through the current host-authorized call chain."""
