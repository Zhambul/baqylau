# Copyright (c) 2026 Zhambyl Yermagambet
"""Yield server-sent frames for one extension's record changes."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from api import sse
from api.extensions.change_models import ExtensionChangeError, ExtensionChangeFrame, ExtensionChangeReset
from core.change_signal import ChangeSignal
from repository.contract.extension_records import ExtensionRecordRepository

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from baqylau_extension_api.models.scopes import ExtensionScope

    from api.extensions.change_models import ExtensionChangeQuery

CHANGES_EVENT = "changes"
RESET_EVENT = "reset"
ERROR_EVENT = "error"


@dataclass(frozen=True)
class ChangeStream:
    """Keep the change stream services and its active head together."""

    records: ExtensionRecordRepository
    changes: ChangeSignal
    history_revision: str
    generation: str


async def change_frames(
    stream: ChangeStream,
    extension_id: str,
    scope: ExtensionScope,
    query: ExtensionChangeQuery,
) -> AsyncGenerator[str]:
    """Yield frames for one extension change stream.

    Yields:
        Encoded change, reset, heartbeat, or error frames.

    """
    try:
        async with aclosing(change_frame_loop(stream, extension_id, scope, query)) as frames:
            async for frame in frames:
                yield frame  # noqa: ASYNC119 -- The caller closes this stream generator.
    except Exception:  # noqa: BLE001 -- The stream headers are already sent.
        yield sse.sse_frame(ERROR_EVENT, ExtensionChangeError(error="stream failed"))


async def change_frame_loop(
    stream: ChangeStream,
    extension_id: str,
    scope: ExtensionScope,
    query: ExtensionChangeQuery,
) -> AsyncGenerator[str]:
    """Read changes only after a change notice.

    Yields:
        Encoded change, reset, or heartbeat frames.

    """
    cursor = query.cursor
    if query.history_revision != stream.history_revision or query.projection_generation != stream.generation:
        cursor = 0
        yield sse.sse_frame(
            RESET_EVENT,
            ExtensionChangeReset(
                history_revision=stream.history_revision, projection_generation=stream.generation, cursor=cursor,
            ),
        )
    with stream.changes.subscribe() as changed:
        while True:
            changed.clear()
            page = await sse.off_loop(stream.records.record_changes, extension_id, scope, stream.generation, cursor)
            if page.changes:
                cursor = page.next_cursor
                yield sse.sse_frame(  # noqa: ASYNC119 -- The caller closes this stream generator.
                    CHANGES_EVENT, ExtensionChangeFrame(records=page.changes, cursor=cursor),
                )
                continue
            try:
                await asyncio.wait_for(changed.wait(), timeout=sse.STREAM_HEARTBEAT_SECONDS)
            except TimeoutError:
                yield sse.BEAT  # noqa: ASYNC119 -- The caller closes this stream generator.
