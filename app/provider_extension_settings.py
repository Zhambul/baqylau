# Copyright (c) 2026 Zhambyl Yermagambet
"""Compose settings controls without starting a daemon or worker from HTTP."""

from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from app.provider_databases import MainDb
from app.provider_extension_controls import ControlPolicy
from app.provider_extension_runtime import Runtime
from extensions.settings_control import SettingsControl
from extensions.settings_control_contract import ExtensionSettingsControl
from repository.impl.sqlite.extension_catalog import SqliteExtensionCatalogRepository


@singleton
def extension_settings_control(runtime: Runtime, database: MainDb, policy: ControlPolicy) -> ExtensionSettingsControl:
    """Use the daemon's existing owner and the normal extension write policy.

    Returns:
        A service which checks ownership when its methods are called.

    """
    return SettingsControl(runtime.manager, SqliteExtensionCatalogRepository(database), policy)


SettingsControlService = Annotated[ExtensionSettingsControl, Depends(extension_settings_control)]
