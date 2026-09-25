# Copyright (c) 2026 Zhambyl Yermagambet
"""Give each extension stage of the engine its failure and health reporter."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from audit.failures import FailureContext
from extensions.pass_health import AuditOnlyHealth

if TYPE_CHECKING:
    from audit.failures import CoalescingFailureRecorder
    from engine.worker import EngineWorker
    from extensions.pass_health import PassHealth


def stage_health(engine_worker: EngineWorker, where: str) -> PassHealth:
    """Write each failure to the audit with its owner, and count it when the engine has durable health.

    Returns:
        The reporter of one stage.

    """
    audit = partial(_audit, engine_worker.interpreter.failures, where)
    tracker = engine_worker.extension_services.health
    return AuditOnlyHealth(audit) if tracker is None else tracker.stage(where, audit)


def _audit(coalescing_failure_recorder: CoalescingFailureRecorder, where: str, owner: str) -> None:
    coalescing_failure_recorder.record(where, FailureContext(source=owner))
