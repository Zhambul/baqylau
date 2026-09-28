# Copyright (c) 2026 Zhambyl Yermagambet
"""Yield server-sent frames for one extension's record changes.

An open stream holds its scope, so the scope's sources stay planned while a
pane or page watches it, also with no coding session in that scope.
"""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from typing import TYPE_CHECKING

from api import sse
from api.extensions.change_models import (
    ExtensionChangeError,
    ExtensionChangeFrame,
    ExtensionChangeQuery,
    ExtensionChangeReset,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from baqylau_extension_api.models.scopes import ExtensionScope

    from api.extensions.change_stream import ChangeStream

CHANGES_EVENT = "changes"
RESET_EVENT = "reset"
ERROR_EVENT = "error"


async def change_frames(
    change_stream: ChangeStream,
    extension_id: str,
    scope: ExtensionScope,
    extension_change_query: ExtensionChangeQuery,
) -> AsyncGenerator[str]:
    """Yield frames for one extension change stream.

    Yields:
        Encoded change, reset, heartbeat, or error frames.

    """
    frame_loop = change_frame_loop(change_stream, extension_id, scope, extension_change_query)
    try:
        with change_stream.scopes.hold_scope(scope):
            # The server sends the headers with the first frame, so an idle stream opens at once.
            yield sse.BEAT  # noqa: ASYNC119 -- The caller closes this stream generator.
            async with aclosing(frame_loop) as frames:
                async for frame in frames:
                    yield frame  # noqa: ASYNC119 -- The caller closes this stream generator.
    except Exception:  # noqa: BLE001 -- The stream headers are already sent.
        yield sse.sse_frame(ERROR_EVENT, ExtensionChangeError(error="stream failed"))


async def change_frame_loop(
    change_stream: ChangeStream,
    extension_id: str,
    scope: ExtensionScope,
    extension_change_query: ExtensionChangeQuery,
) -> AsyncGenerator[str]:
    """Read changes only after a change notice; reset when the owner's live generation changes.

    Yields:
        Encoded change, reset, or heartbeat frames.

    """
    cursor = extension_change_query.cursor
    generation = extension_change_query.projection_generation
    if extension_change_query.history_revision != change_stream.history_revision:
        generation = ""
    with change_stream.changes.subscribe() as changed:
        while True:
            changed.clear()
            live = await sse.off_loop(change_stream.heads.active_generation, extension_id)
            if live != generation:
                generation, cursor = live, 0
                yield sse.sse_frame(  # noqa: ASYNC119 -- The caller closes this stream generator.
                    RESET_EVENT,
                    ExtensionChangeReset(
                        history_revision=change_stream.history_revision, projection_generation=live, cursor=cursor,
                    ),
                )
            page = await sse.off_loop(
                change_stream.records.record_changes, extension_id, scope, generation, cursor,
            )
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
