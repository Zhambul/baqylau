# Copyright (c) 2026 Zhambyl Yermagambet
"""Apply prepared runtime changes before a complete ordered engine work batch."""

from collections.abc import Callable

from core.work_queue import WorkKind, WorkQueue
from extensions.manager_contract import ExtensionRuntimeBoundary

RETRY_SECONDS = 0.1
RETRY_KEY = "extension publication"


class EngineExtensionBoundary:
    """Retain pending core work while startup restoration is still preparing."""

    def __init__(
        self, boundary: ExtensionRuntimeBoundary | None, work_queue: WorkQueue, record_failure: Callable[[], None],
    ) -> None:
        """Use one publication consumer without doing worker preparation here."""
        self._boundary = boundary
        self._work_queue = work_queue
        self._record_failure = record_failure
        self._ready = boundary is None
        self._pending: set[WorkKind] = set()

    def prepare(self, pending: set[WorkKind]) -> set[WorkKind]:
        """Publish before processing, or retain work until a real readiness notice.

        Returns:
            The complete ready batch, excluding lifecycle notices already handled.

        """
        self._pending.update(pending)
        if WorkKind.EXTENSIONS in pending or not self._ready:
            self._advance()
        self._pending.discard(WorkKind.EXTENSIONS)
        if not self._ready:
            return set()
        ready = self._pending
        self._pending = set()
        return ready

    def _advance(self) -> None:
        if self._boundary is None:
            return
        try:
            progress = self._boundary.publish_ready()
        except Exception:  # noqa: BLE001 -- Keep the old readiness state and retry a failed stored commit.
            self._record_failure()
            self._work_queue.schedule(WorkKind.EXTENSIONS, RETRY_SECONDS, key=RETRY_KEY)
            return
        self._ready = progress.allow_processing
        if progress.status == "published":
            self._pending.add(WorkKind.SOURCES)
        if progress.status == "busy":
            self._work_queue.schedule(WorkKind.EXTENSIONS, RETRY_SECONDS, key=RETRY_KEY)
