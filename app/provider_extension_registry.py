# Copyright (c) 2026 Zhambyl Yermagambet
"""Share one active selection and one call-grant ledger within an application."""

from typing import Annotated
from uuid import uuid4

from baqylau_extension_api.runtime.call_grants import HostCallLedger
from fastapi import Depends

from app.injection import singleton
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
