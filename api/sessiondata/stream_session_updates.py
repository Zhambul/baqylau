# Copyright (c) 2026 Zhambyl Yermagambet
"""Read application snapshots and send heartbeats while session data is idle."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from api import sse
from api.application.mapper import preferences as application_mapper
from api.sessiondata import stream_session_models as session_models

if TYPE_CHECKING:
    from domain.ids import SessionId

APPLICATION_EVENT = "application"
APPLICATION_POLL_SECONDS = 1.0


async def wait_frame(
    changed: asyncio.Event,
    service: session_models.SessionSnapshotReader | None,
    session_id: SessionId,
    state: session_models.SessionFrameState,
) -> str | None:
    """Wait for a change notice or a scheduled application read.

    Returns:
        An application frame or heartbeat, or None when neither is needed.

    """
    if changed.is_set():
        # A present notice wins: the data loop reads it, and it sends a due heartbeat too.
        return None
    timeout = sse.heartbeat_wait(state.heartbeat_at, asyncio.get_running_loop().time())
    if service is not None:
        timeout = min(timeout, APPLICATION_POLL_SECONDS)
    try:
        await asyncio.wait_for(changed.wait(), timeout)
    except TimeoutError:
        return await timeout_frame(service, session_id, state)
    return None


async def timeout_frame(
    service: session_models.SessionSnapshotReader | None,
    session_id: SessionId,
    state: session_models.SessionFrameState,
) -> str | None:
    """Read due application data and keep the stream connection active.

    Returns:
        A changed application frame, a due heartbeat, or None.

    """
    now = asyncio.get_running_loop().time()
    application = await session_application_frame(service, session_id, state, now)
    if application[1] is not None:
        return application[1]
    if now - state.heartbeat_at >= sse.STREAM_HEARTBEAT_SECONDS:
        state.heartbeat_at = now
        return sse.BEAT
    return None


async def session_application_frame(
    service: session_models.SessionSnapshotReader | None,
    session_id: SessionId,
    state: session_models.SessionFrameState,
    now: float,
) -> tuple[bool, str | None]:
    """Read and build a changed session application frame when it is due.

    Returns:
        Whether a snapshot was read, and its frame if the snapshot changed.

    """
    due = service is not None and now - state.application_read_at >= APPLICATION_POLL_SECONDS
    if not due or service is None:
        return False, None
    next_application = await sse.off_loop(service.snapshot, session_id)
    state.application_read_at = now
    if next_application == state.application:
        return True, None
    state.application = next_application
    state.heartbeat_at = now
    return True, sse.sse_frame(APPLICATION_EVENT, application_mapper.session_application(state.application))


async def initial_session_state(
    service: session_models.SessionSnapshotReader | None,
    session_id: SessionId,
    cursor: int,
) -> session_models.SessionFrameState:
    """Read the state that a new session stream needs.

    Returns:
        The initial cursor, optional application snapshot, and read times.

    """
    application = None if service is None else await sse.off_loop(service.snapshot, session_id)
    now = asyncio.get_running_loop().time()
    return session_models.SessionFrameState(cursor, application, now, now)
