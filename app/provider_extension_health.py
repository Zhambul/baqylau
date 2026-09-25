# Copyright (c) 2026 Zhambyl Yermagambet
"""Count extension worker failures and disable an extension that fails too often."""

import time
from typing import Annotated

from fastapi import Depends

from app.injection import singleton
from app.provider_databases import MainDb
from app.provider_extension_controls import LifecycleControlService
from app.provider_extension_policy import HealthLimits
from app.provider_interpreter import interpreter
from engine.interpret.loop import Interpreter
from extensions import failure_disable, pass_health
from repository.impl.sqlite.extension_health import SqliteExtensionHealth


@singleton
def extension_health(
    database: MainDb,
    control: LifecycleControlService,
    engine_interpreter: Annotated[Interpreter, Depends(interpreter)],
    policy: HealthLimits,
) -> pass_health.HealthTracker:
    """Keep durable health; at the limit, the host disables the extension and its required dependents.

    Returns:
        The one tracker of the daemon.

    """
    return pass_health.HealthTracker(
        store=SqliteExtensionHealth(database), clock=time.time,
        on_failed=failure_disable.FailureDisable(control, engine_interpreter.failures),
        policy=policy,
    )


Health = Annotated[pass_health.HealthTracker, Depends(extension_health)]
