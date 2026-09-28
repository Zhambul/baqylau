# Copyright (c) 2026 Zhambyl Yermagambet
"""Produce server-sent event frames for all sessions."""

from __future__ import annotations

import asyncio
from contextlib import aclosing
from typing import TYPE_CHECKING

from api import sse
from api.application.mapper import preferences as application_mapper
from api.sessiondata import mapper, stream_global_contract, stream_global_models as global_models
from api.sessiondata.stream_frames import present_frames

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from repository.contract.session_data import AggregateDelta

APPLICATION_EVENT = "application"
RESET_EVENT = "reset"


async def global_frames(sources: global_models.GlobalFrameSources, cursor: int) -> AsyncGenerator[str]:
    """Yield frames for the global session stream.

    Yields:
        Encoded readiness, data, heartbeat, or error frames.

    """
    yield sse.sse_frame("ready", stream_global_contract.ReadyFrame(boot_id=sources.boot_id))
    try:
        async with aclosing(
            global_frame_loop(
                sources.read_model,
                cursor,
                sources.application_preferences,
                sources.application_updates,
            ),
        ) as frames:
            async for frame in frames:
                yield frame  # noqa: ASYNC119 -- The caller closes this stream generator.
    except Exception:  # noqa: BLE001 -- Audit failures after the stream headers have been sent.
        sources.audit.error("", "global stream", stream_global_contract.PathAudit(path="/sessionData/stream"))
        yield sse.sse_frame("error", stream_global_contract.ErrorFrame(error="stream failed"))


async def global_frame_loop(
    session_data_repository: global_models.AggregateDeltaReader,
    cursor: int,
    application_preferences: global_models.ApplicationSnapshotReader,
    application_updates: global_models.RevisionReader,
) -> AsyncGenerator[str]:
    """Read changed data after a notice, with idle connection heartbeats; end with a reset after a view switch.

    The caller must close this generator to release the subscription.

    Yields:
        Encoded application, session-data, or heartbeat frames.

    """
    with application_updates.changes.subscribe() as changed:
        state = await initial_global_state(cursor, application_preferences, application_updates)
        yield sse.sse_frame(APPLICATION_EVENT, application_mapper.global_application(state.application))  # noqa: ASYNC119 -- The caller closes this stream generator.
        while True:
            changed.clear()
            for frame in await global_iteration(
                session_data_repository,
                application_preferences,
                application_updates,
                state,
            ):
                yield frame  # noqa: ASYNC119 -- The caller closes this stream generator.
            if state.reset:
                return
            while not changed.is_set():
                try:
                    await asyncio.wait_for(changed.wait(), sse.STREAM_HEARTBEAT_SECONDS)
                except TimeoutError:
                    state.heartbeat_at = asyncio.get_running_loop().time()
                    yield sse.BEAT  # noqa: ASYNC119 -- The caller closes this stream generator.


async def global_iteration(
    session_data_repository: global_models.AggregateDeltaReader,
    application_preferences: global_models.ApplicationSnapshotReader,
    application_updates: global_models.RevisionReader,
    state: global_models.GlobalFrameState,
) -> tuple[str, ...]:
    """Read and present one global change batch.

    Returns:
        The encoded frames produced by the change batch.

    """
    delta = await sse.off_loop(session_data_repository.changed_after, state.cursor)
    if state.view_revision is None:
        state.view_revision = delta.view_revision
    elif delta.view_revision != state.view_revision:
        state.reset = True
        return (sse.sse_frame(RESET_EVENT, stream_global_contract.ViewReset(view_revision=delta.view_revision)),)
    now = asyncio.get_running_loop().time()
    application = await global_application_frame(application_preferences, application_updates, state, now)
    return present_frames(
        global_delta_frame(delta, state, now),
        application[1],
        global_heartbeat(delta, state, now, application_changed=application[0]),
    )


def global_delta_frame(
    aggregate_delta: AggregateDelta, state: global_models.GlobalFrameState, now: float,
) -> str | None:
    """Build the session-data frame from one nonempty aggregate delta.

    Returns:
        The encoded frame, or None if no session data changed.

    """
    if aggregate_delta.empty:
        return None
    frame = sse.sse_frame(
        "sessionData",
        stream_global_contract.GlobalStreamFrame(
            sessions=tuple(mapper.session(facts) for facts in aggregate_delta.sessions),
            actors=tuple(mapper.actor(row) for row in aggregate_delta.actors),
        ),
        aggregate_delta.cursor,
    )
    state.cursor = aggregate_delta.cursor
    state.heartbeat_at = now
    return frame


async def global_application_frame(
    application_preferences: global_models.ApplicationSnapshotReader,
    application_updates: global_models.RevisionReader,
    state: global_models.GlobalFrameState,
    now: float,
) -> tuple[bool, str | None]:
    """Read and build a changed global application frame.

    Returns:
        A change flag and encoded frame, or (False, None) if unchanged.

    """
    next_revision = application_updates.revision()
    if next_revision == state.application_revision:
        return False, None
    state.application_revision = next_revision
    state.application = await sse.off_loop(application_preferences.snapshot)
    state.heartbeat_at = now
    return True, sse.sse_frame(APPLICATION_EVENT, application_mapper.global_application(state.application))


def global_heartbeat(
    aggregate_delta: AggregateDelta,
    state: global_models.GlobalFrameState,
    now: float,
    *,
    application_changed: bool,
) -> str | None:
    """Return a heartbeat when the change batch produced no other frames.

    Returns:
        A heartbeat when the change batch produced no other frames.

    """
    if application_changed or not aggregate_delta.empty or now - state.heartbeat_at < sse.STREAM_HEARTBEAT_SECONDS:
        return None
    state.heartbeat_at = now
    return sse.BEAT


async def initial_global_state(
    cursor: int,
    application_preferences: global_models.ApplicationSnapshotReader,
    application_updates: global_models.RevisionReader,
) -> global_models.GlobalFrameState:
    """Read the state that a new global stream needs.

    Returns:
        The initial cursor, application snapshot, revision, and heartbeat time.

    """
    application_revision = application_updates.revision()
    application = await sse.off_loop(application_preferences.snapshot)
    heartbeat_at = asyncio.get_running_loop().time()
    return global_models.GlobalFrameState(cursor, application, application_revision, heartbeat_at)
