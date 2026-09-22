# Copyright (c) 2026 Zhambyl Yermagambet
"""Drain ready stages in order and retain failed work for a known retry."""

from collections.abc import Callable
from dataclasses import dataclass

from audit.failures import CoalescingFailureRecorder, FailureContext
from core.work_queue import WorkKind, WorkQueue


def ready_stages(pending: set[WorkKind]) -> set[WorkKind]:
    """Include dependent stages once without adding a core scan to an extension deadline.

    Returns:
        The complete ordered work selection for this pass.

    """
    if WorkKind.SOURCES in pending:
        pending.discard(WorkKind.EXTENSION_SOURCES)
        pending.add(WorkKind.RAW)
    if WorkKind.EXTENSION_SOURCES in pending:
        pending.add(WorkKind.RAW)
    if WorkKind.RAW in pending:
        pending.add(WorkKind.CANONICAL)
    return pending


@dataclass(frozen=True)
class EngineWorkBatch:
    """Keep stage retry policy separate from the engine's resource ownership."""

    queue: WorkQueue
    failures: CoalescingFailureRecorder

    def run(
        self, pending: set[WorkKind], stopped: Callable[[], bool],
        stage: Callable[[WorkKind], None],
    ) -> None:
        """Continue unrelated stages after a failed stage while retaining its pending work."""
        for kind in WorkKind:
            if kind not in pending or stopped():
                continue
            try:
                stage(kind)
            except Exception:  # noqa: BLE001 -- Record stage failures and retry their persisted work.
                self.retry({kind}, "engine work")

    def retry(self, pending: set[WorkKind], where: str) -> None:
        """Keep all affected stages after a failed call or runtime capture."""
        self.failures.record(where, FailureContext())
        for kind in pending:
            self.queue.schedule(kind, 1.0, key="retry")
