# Copyright (c) 2026 Zhambyl Yermagambet
"""Check session stream reads after change notices."""

import asyncio
from unittest.mock import Mock, call

import pytest

from api import sse
from api.sessiondata.stream_session_frames import session_frames
from api.sessiondata.stream_session_models import SessionStreamPosition, SessionStreamServices
from core.change_signal import ChangeSignal
from repository.contract.session_data import SessionDataRepository, SessionDelta
from tests.canonical_sessiondata_api_values import FACTS, SESSION

# The first read has no entry row; later reads name the row that the first read reached.
EXPECTED_READS = (call(SESSION, 0, None), call(SESSION, 1, 0))

IDLE_CHECK_SECONDS = 0.7
TEST_HEARTBEAT_SECONDS = 0.02


async def _check_session_notices() -> None:
    repository = Mock(spec=SessionDataRepository)
    repository.delta.return_value = SessionDelta(FACTS, (), (), 1)
    signal = ChangeSignal()
    audit = Mock()
    stream = session_frames(SessionStreamServices(repository, audit, changes=signal), SESSION, SessionStreamPosition(0))
    assert "event: sessionData" in await anext(stream)
    repository.delta.return_value = SessionDelta(None, (), (), 1)
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(IDLE_CHECK_SECONDS)
    repository.delta.assert_called_once_with(SESSION, 0, None)
    assert not pending.done()
    repository.delta.return_value = SessionDelta(FACTS, (), (), 2)
    await asyncio.to_thread(signal.publish)
    assert "id: 2" in await asyncio.wait_for(pending, 1)
    assert tuple(repository.delta.call_args_list) == EXPECTED_READS
    await stream.aclose()
    signal.publish()
    audit.error.assert_not_called()


async def _check_session_heartbeat() -> None:
    repository = Mock(spec=SessionDataRepository)
    repository.delta.return_value = SessionDelta(None, (), (), 0)
    stream = session_frames(SessionStreamServices(repository, Mock()), SESSION, SessionStreamPosition(0))
    assert await asyncio.wait_for(anext(stream), 1) == sse.BEAT
    assert await asyncio.wait_for(anext(stream), 1) == sse.BEAT
    repository.delta.assert_called_once_with(SESSION, 0, None)
    await stream.aclose()


def test_session_stream_waits_for_a_change_notice() -> None:
    """Do not read the database again while the stream is idle."""
    asyncio.run(_check_session_notices())


def test_session_heartbeat_avoids_database_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Send keep-alive frames without database reads."""
    monkeypatch.setattr(sse, "STREAM_HEARTBEAT_SECONDS", TEST_HEARTBEAT_SECONDS)
    asyncio.run(_check_session_heartbeat())
