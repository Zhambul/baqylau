# Copyright (c) 2026 Zhambyl Yermagambet
"""Produce server-sent event frames for one session."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from typing import TYPE_CHECKING

from api import sse
from api.application.mapper import preferences as application_mapper
from api.sessiondata import (
    mapper,
    stream_session_contract,
    stream_session_models as session_models,
    stream_session_updates as application_updates,
)
from api.sessiondata.stream_frames import present_frames
from core.change_signal import ChangeSignal

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from domain.ids import SessionId
    from repository.contract.session_data import SessionDelta

APPLICATION_EVENT = "application"
RESET_EVENT = "reset"


async def session_frames(
    services: session_models.SessionStreamServices,
    session_id: SessionId,
    position: session_models.SessionStreamPosition,
    *,
    include_application: bool = True,
) -> AsyncGenerator[str]:
    """Yield frames for one session stream.

    Yields:
        Encoded data, heartbeat, or error frames.

    """
    try:
        async with aclosing(
            session_frame_loop(
                services.read_model,
                session_id,
                position,
                services.session_application if include_application else None,
                change_signal=services.changes,
            ),
        ) as frames:
            async for frame in frames:
                yield frame  # noqa: ASYNC119 -- The caller closes this stream generator.
    except Exception:  # noqa: BLE001 -- Audit failures after the stream headers have been sent.
        services.audit.error(
            str(session_id),
            "session data stream",
            stream_session_contract.SessionAudit(session_id=session_id),
        )
        yield sse.sse_frame("error", stream_session_contract.ErrorFrame(error="stream failed"))


async def session_frame_loop(
    session_data_repository: session_models.SessionDeltaReader,
    session_id: SessionId,
    position: session_models.SessionStreamPosition,
    session_application_service: session_models.SessionSnapshotReader | None,
    *,
    change_signal: ChangeSignal | None = None,
) -> AsyncGenerator[str]:
    """Read session data only after a change notice; end with a reset after a view switch.

    Yields:
        Encoded application, session-data, heartbeat, or reset frames.

    """
    signal = ChangeSignal() if change_signal is None else change_signal
    with signal.subscribe() as changed:
        state = await application_updates.initial_session_state(
            session_application_service, session_id, position.cursor,
        )
        state.view_revision = position.view_revision
        state.entry_cursor = position.entry_cursor
        if state.application is not None:
            yield sse.sse_frame(APPLICATION_EVENT, application_mapper.session_application(state.application))  # noqa: ASYNC119 -- The caller closes this stream generator.
        while True:
            changed.clear()
            for frame in await session_iteration(
                session_data_repository, session_application_service, session_id, state,
            ):
                yield frame  # noqa: ASYNC119 -- The caller closes this stream generator.
            if state.reset:
                return
            while not changed.is_set():
                idle_frame = await application_updates.wait_frame(
                    changed, session_application_service, session_id, state,
                )
                if idle_frame is not None:
                    yield idle_frame  # noqa: ASYNC119 -- The caller closes this stream generator.


async def session_iteration(
    session_data_repository: session_models.SessionDeltaReader,
    session_application_service: session_models.SessionSnapshotReader | None,
    session_id: SessionId,
    state: session_models.SessionFrameState,
) -> tuple[str, ...]:
    """Read and present changes after a session notice.

    Returns:
        The encoded frames produced by the change batch.

    """
    delta = await sse.off_loop(session_data_repository.delta, session_id, state.cursor, state.entry_cursor)
    state.entry_cursor = delta.entry_cursor
    if state.view_revision is None:
        state.view_revision = delta.view_revision
    elif delta.view_revision != state.view_revision:
        state.reset = True
        reset = stream_session_contract.ViewReset(view_revision=delta.view_revision)
        return (sse.sse_frame(RESET_EVENT, reset),)
    now = asyncio.get_running_loop().time()
    application = await application_updates.session_application_frame(
        session_application_service, session_id, state, now,
    )
    return present_frames(
        session_delta_frame(delta, state, now),
        application[1],
        session_heartbeat(delta, state, now, application_checked=application[0]),
    )


def session_delta_frame(session_delta: SessionDelta, state: session_models.SessionFrameState, now: float) -> str | None:
    """Build the session-data frame from one nonempty delta.

    Returns:
        The encoded frame, or None if no session data changed.

    """
    if session_delta.empty:
        return None
    frame = sse.sse_frame(
        "sessionData",
        stream_session_contract.SessionStreamFrame(
            session=None if session_delta.session is None else mapper.session(session_delta.session),
            actors=tuple(mapper.actor(row) for row in session_delta.actors),
            entries=tuple(mapper.entry(session_entry) for session_entry in session_delta.entries),
        ),
        session_delta.cursor,
    )
    state.cursor = session_delta.cursor
    state.heartbeat_at = now
    return frame


def session_heartbeat(
    session_delta: SessionDelta,
    state: session_models.SessionFrameState,
    now: float,
    *,
    application_checked: bool,
) -> str | None:
    """Return a heartbeat when the read did not produce other frames.

    Returns:
        A heartbeat when the read did not produce other frames.

    """
    if application_checked or not session_delta.empty or now - state.heartbeat_at < sse.STREAM_HEARTBEAT_SECONDS:
        return None
    state.heartbeat_at = now
    return sse.BEAT
