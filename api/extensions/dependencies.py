# Copyright (c) 2026 Zhambyl Yermagambet
"""Require daemon ownership for runtime reads without creating a manager in HTTP."""

from typing import Annotated

from fastapi import Depends

from app.provider_extension_runtime import Runtime
from extensions.lifecycle_control_contract import LifecycleUnavailableError
from extensions.manager_contract import ExtensionManager


def extension_manager(runtime: Runtime) -> ExtensionManager:
    """Borrow the daemon-owned manager; never activate a worker in a dependency.

    Returns:
        The manager owned by the application worker lifecycle.

    Raises:
        LifecycleUnavailableError: If no daemon manager was opened.

    """
    if runtime.manager is None:
        message = "extension runtime state requires a running daemon manager"
        raise LifecycleUnavailableError(message)
    return runtime.manager


Manager = Annotated[ExtensionManager, Depends(extension_manager)]
