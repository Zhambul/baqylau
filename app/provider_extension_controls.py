# Copyright (c) 2026 Zhambyl Yermagambet
"""Compose checked lifecycle requests and an explicit extension write policy."""

import os
from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from app.provider_databases import MainDb
from app.provider_extension_runtime import Runtime
from extensions.configuration import configured_read_only
from extensions.control_policy import ExtensionControlPolicy
from extensions.lifecycle_control import LifecycleControl
from extensions.lifecycle_control_contract import ExtensionLifecycleControl
from repository.impl.sqlite.extension_catalog import SqliteExtensionCatalogRepository


@singleton
def extension_control_policy() -> ExtensionControlPolicy:
    """Freeze management policy for this application instance.

    Returns:
        Extension write admission policy, not a global Baqylau read-only mode.

    """
    return ExtensionControlPolicy(read_only=configured_read_only(os.environ))


ControlPolicy = Annotated[ExtensionControlPolicy, Depends(extension_control_policy)]


@singleton
def extension_lifecycle_control(runtime: Runtime, database: MainDb, policy: ControlPolicy) -> ExtensionLifecycleControl:
    """Build controls without starting or requiring a manager during graph construction.

    Returns:
        A service which checks for daemon ownership when a request uses it.

    """
    return LifecycleControl(runtime.manager, SqliteExtensionCatalogRepository(database), policy)


LifecycleControlService = Annotated[ExtensionLifecycleControl, Depends(extension_lifecycle_control)]
