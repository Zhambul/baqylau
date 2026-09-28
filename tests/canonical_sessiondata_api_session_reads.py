# Copyright (c) 2026 Zhambyl Yermagambet
"""Read test frames from one session stream."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from api.sessiondata import streams
from tests import (
    canonical_sessiondata_api_stream_models as stream_models,
    canonical_sessiondata_api_values as api_values,
)

if TYPE_CHECKING:
    from repository.impl.sqlite.session_data import SqliteSessionDataRepository


async def read_application_and_session_frames(
    read_model: SqliteSessionDataRepository,
) -> tuple[str, str]:
    """Read the initial application and session snapshots.

    Returns:
        The application frame followed by the session frame.

    """
    reader = stream_models.FrameReader(
        streams.session_frames(
            streams.SessionStreamServices(
                read_model, stream_models.SilentAudit(), stream_models.SessionApplicationSnapshots(),
            ),
            api_values.SESSION,
            streams.SessionStreamPosition(0),
        ),
    )
    application = await reader.next()
    session_data = await reader.next()
    await reader.aclose()
    return application, session_data


async def read_reconnected_frame(
    read_model: SqliteSessionDataRepository,
    cursor: int,
) -> str:
    """Read the first session frame after reconnecting at the supplied cursor.

    Returns:
        The first frame from the reconnected stream.

    """
    reader = stream_models.FrameReader(
        streams.session_frames(
            streams.SessionStreamServices(read_model, stream_models.SilentAudit()),
            api_values.SESSION,
            streams.SessionStreamPosition(cursor),
        ),
    )
    frame = await reader.next()
    await reader.aclose()
    return frame


async def read_first_frame_and_confirm_no_update(
    read_model: SqliteSessionDataRepository,
) -> str:
    """Read the initial session frame and check that no update follows.

    Returns:
        The initial session frame.

    """
    reader = stream_models.FrameReader(
        streams.session_frames(
            streams.SessionStreamServices(read_model, stream_models.SilentAudit()),
            api_values.SESSION,
            streams.SessionStreamPosition(0),
        ),
    )
    first = await reader.next()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(reader.asend(), api_values.NO_UPDATE_WAIT_SECONDS)
    await reader.aclose()
    return first
