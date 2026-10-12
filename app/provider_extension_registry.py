# Copyright (c) 2026 Zhambyl Yermagambet
"""Share one active selection, one call-grant ledger, and one query host within an application."""

from typing import Annotated
from uuid import uuid4

from baqylau_extension_api.runtime.call_grants import HostCallLedger
from fastapi import Depends

from app import provider_audit_storage
from app.injection import singleton
from app.provider_extension_policy import WorkerLimits
from audit.failures import CoalescingFailureRecorder
from extensions.query_authority import QueryAuthority, QueryHost
from extensions.registry import ActiveExtensionRegistry
from extensions.registry_contract import ExtensionRegistry


@singleton
def extension_registry() -> ExtensionRegistry:
    """Build an empty registry with no worker ownership.

    Returns:
        The selection which the daemon manager will restore and publish.

    """
    return ActiveExtensionRegistry(f"unprepared-{uuid4().hex}")


Registry = Annotated[ExtensionRegistry, Depends(extension_registry)]


@singleton
def extension_call_ledger() -> HostCallLedger:
    """Keep authority outside extension request bodies.

    Returns:
        The shared ledger used by private workers and their peer callbacks.

    """
    return HostCallLedger()


CallLedger = Annotated[HostCallLedger, Depends(extension_call_ledger)]


@singleton
def extension_query_failures(audit: provider_audit_storage.Recorder) -> CoalescingFailureRecorder:
    """Record the first refused query of a burst, then count the repeats.

    Returns:
        The failure record of the query route.

    """
    return CoalescingFailureRecorder(audit, "extension query")


QueryFailures = Annotated[CoalescingFailureRecorder, Depends(extension_query_failures)]


@singleton
def extension_query_host(ledger: CallLedger, limits: WorkerLimits, failures: QueryFailures) -> QueryHost:
    """Use the shared call ledger, the host's call deadline, and the shared failure record.

    Returns:
        The query host of the query route.

    """
    return QueryHost(QueryAuthority(ledger, limits.request_seconds), failures)


ExtensionQueryHost = Annotated[QueryHost, Depends(extension_query_host)]
