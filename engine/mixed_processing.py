# Copyright (c) 2026 Zhambyl Yermagambet
"""Yield mixed raw work between complete originals and request an explicit continuation."""

from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

from core.work_queue import WorkKind, WorkQueue
from extensions.interpretation_contract import CoreInterpretation
from extensions.processing_contract import ExtensionProcessingBatch

MIXED_SECONDS = 1.0
CONTINUATION_SECONDS = 0.01
CONTINUATION_KEY = "mixed raw continuation"


@dataclass(frozen=True)
class ProcessingSlice:
    """Check elapsed time only where a complete original can safely yield."""

    expires_at: float

    def expired(self) -> bool:
        """Check the monotonic end of this processing interval.

        Returns:
            True when no new original should start in this interval.

        """
        return monotonic() >= self.expires_at


def read_mixed(
    batch: ExtensionProcessingBatch, core: CoreInterpretation, work_queue: WorkQueue, stopped: Callable[[], bool],
) -> None:
    """Process at most one bounded page before reactions and runtime publication.

    This cooperative interval cannot interrupt a worker call or transaction.
    One completed original can exceed it. Timed yield starts only after
    the first original so that a slow page read cannot prevent progress.
    """
    interval = ProcessingSlice(monotonic() + MIXED_SECONDS)
    completed = batch.interpret_pending(core, stopped, yield_requested=interval.expired)
    pending = not stopped() and completed > 0
    delay = CONTINUATION_SECONDS if pending else None
    work_queue.set_deadline(WorkKind.RAW, delay, CONTINUATION_KEY)
