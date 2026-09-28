# Copyright (c) 2026 Zhambyl Yermagambet
"""Compose checked lifecycle requests under the extension write policy."""

from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from app.provider_databases import MainDb
from app.provider_extension_policy import ControlPolicy
from app.provider_extension_runtime import Runtime
from extensions.lifecycle_control import LifecycleControl
from extensions.lifecycle_control_contract import ExtensionLifecycleControl
from repository.impl.sqlite.extension_catalog import SqliteExtensionCatalogRepository
from repository.impl.sqlite.record_migrations import SqliteRecordMigrationStore


@singleton
def extension_lifecycle_control(runtime: Runtime, database: MainDb, policy: ControlPolicy) -> ExtensionLifecycleControl:
    """Build controls without starting or requiring a manager during graph construction.

    Returns:
        A service which checks for daemon ownership when a request uses it.

    """
    return LifecycleControl(
        runtime.manager, SqliteExtensionCatalogRepository(database), policy, SqliteRecordMigrationStore(database),
    )


LifecycleControlService = Annotated[ExtensionLifecycleControl, Depends(extension_lifecycle_control)]
