# Copyright (c) 2026 Zhambyl Yermagambet
"""Canonical sessiondata api stream changes."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from api.sessiondata import streams
from tests import (
    canonical_sessiondata_api_stream_models as stream_models,
    canonical_sessiondata_api_values as api_values,
)

if TYPE_CHECKING:
    from dashboard.services.application_updates import ApplicationUpdateState
    from repository.impl.sqlite.session_data import SqliteSessionDataRepository


async def read_changed_application_frame(
    read_model: SqliteSessionDataRepository,
    application: stream_models.ApplicationSnapshots,
    updates: ApplicationUpdateState,
) -> tuple[str, str, int]:
    """Read an application frame before and after an update signal.

    Returns:
        The initial frame, changed frame, and read count before the update.

    """
    reader = stream_models.FrameReader(
        streams.global_frames(
            streams.GlobalFrameSources(
                read_model,
                stream_models.SilentAudit(),
                "boot-one",
                application,
                updates,
            ),
            0,
        ),
    )
    await reader.next()
    initial = await reader.next()
    pending = asyncio.create_task(reader.asend())
    await asyncio.sleep(api_values.NO_UPDATE_WAIT_SECONDS)
    stable_reads = application.reads
    application.enabled = False
    updates.publish()
    changed = await asyncio.wait_for(pending, 3)
    await reader.aclose()
    return initial, changed, stable_reads


async def read_failed_session_frame(audit: stream_models.SilentAudit) -> str:
    """Read the frame produced when the session read model fails.

    Returns:
        The failure frame.

    """
    reader = stream_models.FrameReader(
        streams.session_frames(
            streams.SessionStreamServices(stream_models.BrokenReadModel(), audit),
            api_values.SESSION,
            streams.SessionStreamPosition(0),
        ),
    )
    frame = await reader.next()
    await reader.aclose()
    return frame
