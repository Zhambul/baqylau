# Copyright (c) 2026 Zhambyl Yermagambet
"""Write hook raw events in arrival order, after the hook has its reply.

A harness waits for each hook, and a hook waited for its raw events to be
written. That write waits for the engine's write lock, so a hook took about
100 ms and up to seconds, and a Chrome approval waited for two hooks. Now the
daemon replies first, and one thread writes the deliveries in the order they
came. Deliveries that wait together are written in one transaction.
"""

from __future__ import annotations

import queue
import threading
from typing import TYPE_CHECKING, Protocol

from audit.harness_documents import HarnessErrorAudit

if TYPE_CHECKING:
    from collections.abc import Sequence

    from audit.failures import ErrorRecorder
    from harness.models.raw_events import RawEvent

HOOK_WRITE = "hook delivery"


class RawEventWriter(Protocol):
    """Write raw events in one transaction."""

    def record(self, raw_events: Sequence[RawEvent]) -> None:
        """Write raw events."""
        ...


class OrderedHookRecorder(RawEventWriter):
    """Write each hook delivery's raw events on one thread, in arrival order."""

    def __init__(self, raw_event_writer: RawEventWriter, error_recorder: ErrorRecorder) -> None:
        """Start the writer thread."""
        self._raw_events = raw_event_writer
        self._audit = error_recorder
        # None asks the writer to stop after the deliveries before it.
        self._deliveries: queue.Queue[tuple[RawEvent, ...] | None] = queue.Queue()
        self._writer = threading.Thread(target=self._write_until_closed, name="baqylau-hook-recorder", daemon=True)
        self._writer.start()

    def record(self, raw_events: Sequence[RawEvent]) -> None:
        """Queue one delivery's raw events, and return before they are written."""
        if raw_events:
            self._deliveries.put(tuple(raw_events))

    def flush(self) -> None:
        """Wait until each delivery that was queued before this call is written."""
        self._deliveries.join()

    def close(self) -> None:
        """Write the queued deliveries, then stop the writer."""
        self._deliveries.put(None)
        self._writer.join()

    def _write_until_closed(self) -> None:
        closed = False
        while not closed:
            batch = [self._deliveries.get()]
            batch.extend(self._waiting())
            deliveries = [delivery for delivery in batch if delivery is not None]
            closed = len(deliveries) < len(batch)
            self._write(deliveries)
            for _ in batch:
                self._deliveries.task_done()

    def _waiting(self) -> list[tuple[RawEvent, ...] | None]:
        waiting: list[tuple[RawEvent, ...] | None] = []
        while True:
            try:
                waiting.append(self._deliveries.get_nowait())
            except queue.Empty:
                return waiting

    def _write(self, deliveries: list[tuple[RawEvent, ...]]) -> None:
        """Write the deliveries together, or each alone when one of them fails.

        A delivery that fails alone, for example a raw event id that came again
        with different bytes, is audited, and the others are still written.
        """
        if not deliveries:
            return
        try:
            self._raw_events.record([raw_event for delivery in deliveries for raw_event in delivery])
        except Exception as error:  # noqa: BLE001 -- A failed write must not stop the writer; it is audited.
            if len(deliveries) > 1:
                for delivery in deliveries:
                    self._write([delivery])
                return
            harness = deliveries[0][0].harness
            failure = HarnessErrorAudit(harness=harness, error=repr(error))
            self._audit.error("", HOOK_WRITE, failure)
