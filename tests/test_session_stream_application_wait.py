# Copyright (c) 2026 Zhambyl Yermagambet
"""Check application reads during a session stream wait."""

import asyncio
from unittest.mock import Mock

import pytest

from api import sse
from api.sessiondata import stream_session_frames, stream_session_models, stream_session_updates
from repository.contract.session_data import SessionDelta
from tests.canonical_sessiondata_api_stream_models import SessionApplicationSnapshots
from tests.canonical_sessiondata_api_values import FACTS, SESSION


@pytest.mark.parametrize("changed", [True, False])
def test_timeout_emits_application_or_heartbeat(*, changed: bool) -> None:
    """Send changed application data, or a heartbeat for unchanged data."""
    application = SessionApplicationSnapshots().snapshot(SESSION)
    service = Mock()
    service.snapshot.return_value = application
    state = stream_session_models.SessionFrameState(0, None if changed else application, 0, 0)
    frame = asyncio.run(stream_session_updates.timeout_frame(service, SESSION, state))
    service.snapshot.assert_called_once_with(SESSION)
    assert frame is not None
    assert ("event: application" in frame, frame == sse.BEAT) == (changed, not changed)
    assert state.application == application and state.heartbeat_at > 0


def test_change_notice_does_not_read_application() -> None:
    """Return to the data loop as soon as a change notice is present."""
    changed = asyncio.Event()
    changed.set()
    service = Mock()
    state = stream_session_models.SessionFrameState(0, None, 0, 0)
    assert asyncio.run(stream_session_updates.wait_frame(changed, service, SESSION, state)) is None
    service.snapshot.assert_not_called()


async def _read_without_application() -> None:
    repository = Mock()
    repository.delta.return_value = SessionDelta(FACTS, (), (), 1)
    application = Mock()
    services = stream_session_models.SessionStreamServices(repository, Mock(), application)
    position = stream_session_models.SessionStreamPosition(0)
    stream = stream_session_frames.session_frames(services, SESSION, position, include_application=False)
    assert "event: sessionData" in await anext(stream)
    await stream.aclose()
    application.snapshot.assert_not_called()


def test_application_can_be_excluded() -> None:
    """Do not read application state when the client excludes it."""
    asyncio.run(_read_without_application())


async def _due_heartbeat() -> str | None:
    now = asyncio.get_running_loop().time()
    state = stream_session_models.SessionFrameState(0, None, now - sse.STREAM_HEARTBEAT_SECONDS, now)
    waiting = stream_session_updates.wait_frame(asyncio.Event(), None, SESSION, state)
    return await asyncio.wait_for(waiting, 1)


def test_due_heartbeat_is_sent_at_once() -> None:
    """A change notice that brought no frame does not move the next heartbeat.

    Each notice started the full wait again, so a quiet session's stream sent
    its first beat after up to twice the heartbeat time.
    """
    assert asyncio.run(_due_heartbeat()) == sse.BEAT
