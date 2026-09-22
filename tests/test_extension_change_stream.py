# Copyright (c) 2026 Zhambyl Yermagambet
"""Check extension change streams after change notices."""

import asyncio
from unittest.mock import Mock

import pytest
from baqylau_extension_api.models import documents, records, scopes

from api import sse
from api.extensions.change_frames import ChangeStream, change_frame_loop
from api.extensions.change_models import ExtensionChangeQuery
from core.change_signal import ChangeSignal
from repository.contract.extension_records import ExtensionRecordChanges, ExtensionRecordRepository

OWNER = "test.owner"
COLLECTION = "test.owner.notes"
SCOPE = scopes.InstallationScope()
DIGEST = "a" * 64
SCHEMA_REF = documents.SchemaRef(owner=OWNER, name="text", version=1, digest=DIGEST)
KEY = records.RecordKey(owner=OWNER, collection=COLLECTION, scope=SCOPE, key="note-1")
STATE = records.StoredRecord(
    key=KEY,
    revision=1,
    document=documents.EncodedDocument(schema_ref=SCHEMA_REF, json_text='"x"'),
    summary="x",
)
IDLE_CHECK_SECONDS = 0.05
TEST_HEARTBEAT_SECONDS = 0.02


def a_query(**overrides: object) -> ExtensionChangeQuery:
    """Build a change query for the installation scope.

    Returns:
        The query with the supplied overrides.

    """
    values: dict[str, object] = {"scope": SCOPE.model_dump_json()}
    values.update(overrides)
    return ExtensionChangeQuery.model_validate(values)


def a_stream(repository: Mock, signal: ChangeSignal) -> ChangeStream:
    """Build the default active-head change stream.

    Returns:
        The stream over the supplied repository and signal.

    """
    return ChangeStream(records=repository, changes=signal, history_revision="default", generation="default")


async def _check_change_notice() -> None:
    repository = Mock(spec=ExtensionRecordRepository)
    repository.record_changes.return_value = ExtensionRecordChanges((), 0)
    signal = ChangeSignal()
    stream = change_frame_loop(a_stream(repository, signal), OWNER, SCOPE, a_query())
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(IDLE_CHECK_SECONDS)
    repository.record_changes.assert_called_once_with(OWNER, SCOPE, "default", 0)

    repository.record_changes.return_value = ExtensionRecordChanges((STATE,), 1)
    await asyncio.to_thread(signal.publish)
    frame = await asyncio.wait_for(pending, 1)
    assert "event: changes" in frame
    assert '"note-1"' in frame
    await stream.aclose()


async def _check_reset() -> None:
    repository = Mock(spec=ExtensionRecordRepository)
    repository.record_changes.return_value = ExtensionRecordChanges((), 0)
    signal = ChangeSignal()
    query = a_query(projection_generation="other")
    stream = change_frame_loop(a_stream(repository, signal), OWNER, SCOPE, query)
    frame = await asyncio.wait_for(anext(stream), 1)
    assert "event: reset" in frame
    assert '"projection_generation":"default"' in frame
    await stream.aclose()


async def _check_heartbeat() -> None:
    repository = Mock(spec=ExtensionRecordRepository)
    repository.record_changes.return_value = ExtensionRecordChanges((), 0)
    stream = change_frame_loop(a_stream(repository, ChangeSignal()), OWNER, SCOPE, a_query())
    assert await asyncio.wait_for(anext(stream), 1) == sse.BEAT
    repository.record_changes.assert_called_once_with(OWNER, SCOPE, "default", 0)
    await stream.aclose()


def test_change_stream_waits_for_a_change_notice() -> None:
    """Do not read the database again while the stream is idle."""
    asyncio.run(_check_change_notice())


def test_change_stream_resets_an_unknown_generation() -> None:
    """An unknown history or generation restarts the client from the active head."""
    asyncio.run(_check_reset())


def test_change_stream_sends_a_heartbeat(monkeypatch: pytest.MonkeyPatch) -> None:
    """An idle stream sends a keep-alive beat."""
    monkeypatch.setattr(sse, "STREAM_HEARTBEAT_SECONDS", TEST_HEARTBEAT_SECONDS)
    asyncio.run(_check_heartbeat())
