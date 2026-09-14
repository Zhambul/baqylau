# Copyright (c) 2026 Zhambyl Yermagambet
"""Wait for the native records that confirm one control request.

A native key write or paste that succeeds says only that the terminal took the
input. It does not say that OpenCode2 acted on it. Every control here therefore
reads the session's own event log from the byte position it held BEFORE the
request: an old matching record cannot confirm a new request.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from harness.impl.opencode2.records import NativeRecord
from harness.impl.opencode2.sources import OpenCodeSource

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from harness.models.raw_events import RawEventSourceContext

READ_INTERVAL_SECONDS = 0.1


def read(raw_event_source_context: RawEventSourceContext) -> Iterator[NativeRecord]:
    """Read the complete native history in bounded pages.

    Yields:
        Saved native records in their original order.

    """
    source = OpenCodeSource(raw_event_source_context)
    after_position = None
    while records := source.read(after_position):
        for record in records:
            yield NativeRecord.model_validate_json(record.payload)
        after_position = records[-1].source_position


def position(source_reference: str) -> str | None:
    """Read the log position that a control request starts from.

    Returns:
        The last read position, or None when the log is unavailable.

    """
    try:
        size = Path(source_reference).stat().st_size
    except OSError:
        return None
    return str(max(size - 1, 0))


def observed(
    raw_event_source_context: RawEventSourceContext,
    after_position: str | None,
    matches: Callable[[NativeRecord], bool],
    seconds: float,
) -> bool:
    """Wait for a new native record that confirms the request.

    Returns:
        True when a matching record arrives inside the time limit.

    """
    source = OpenCodeSource(raw_event_source_context)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        records = source.read(after_position)
        if any(matches(NativeRecord.model_validate_json(record.payload)) for record in records):
            return True
        if records:
            after_position = records[-1].source_position or after_position
        time.sleep(READ_INTERVAL_SECONDS)
    return False
