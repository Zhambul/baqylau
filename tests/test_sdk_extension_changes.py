# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the typed SDK extension change stream read."""

from __future__ import annotations

from typing import cast

import pytest
from baqylau_extension_api.models import documents, records, scopes

from api.extensions.change_models import ExtensionChangeFrame, ExtensionChangeReset
from sdk.client_extension_changes import ExtensionChangesResource
from sdk.transport import ApiFailureError, HttpTransport
from tests.sdk_test_transports import EventStreamTransport

OWNER = "test.owner"
COLLECTION = "test.owner.notes"
SCOPE = scopes.InstallationScope()
DIGEST_LENGTH = 64
DIGEST = "a" * DIGEST_LENGTH
SCHEMA_REF = documents.SchemaRef(owner=OWNER, name="text", version=1, digest=DIGEST)
KEY = records.RecordKey(owner=OWNER, collection=COLLECTION, scope=SCOPE, key="note-1")
STATE = records.StoredRecord(
    key=KEY,
    revision=3,
    document=documents.EncodedDocument(schema_ref=SCHEMA_REF, json_text='"x"'),
    summary="x",
)
CHANGE_CURSOR = 3


def resource_for(transport_handle: EventStreamTransport) -> ExtensionChangesResource:
    """Build the change resource over a fake event-stream transport.

    Returns:
        The resource over the supplied transport.

    """
    return ExtensionChangesResource(cast("HttpTransport", transport_handle))


def lines_for(event: str, payload: str) -> list[str]:
    """Build the SSE lines for one event.

    Returns:
        The event lines ending the frame.

    """
    return [f"event: {event}", f"data: {payload}", ""]


def test_sdk_change_read_returns_the_change_frame() -> None:
    """The SDK returns the typed change frame and its cursor."""
    frame = ExtensionChangeFrame(records=(STATE,), cursor=CHANGE_CURSOR)
    transport_handle = EventStreamTransport(lines_for("changes", frame.model_dump_json()))
    update = resource_for(transport_handle).next(OWNER, SCOPE)

    assert update.cursor == CHANGE_CURSOR
    assert update.frame == frame
    path, headers = transport_handle.requests[0]
    assert path.startswith(f"/api/extensions/{OWNER}/changes?")
    assert headers is None


def test_sdk_change_read_returns_the_reset_frame() -> None:
    """The SDK returns a snapshot reset instruction."""
    reset = ExtensionChangeReset(history_revision="default", projection_generation="default", cursor=0)
    transport_handle = EventStreamTransport(lines_for("reset", reset.model_dump_json()))
    update = resource_for(transport_handle).next(OWNER, SCOPE, projection_generation="other")

    assert update.cursor == 0
    assert update.frame == reset


def test_sdk_change_read_reports_a_stream_error() -> None:
    """An error frame is a typed SDK failure."""
    transport_handle = EventStreamTransport(lines_for("error", '{"error":"stream failed"}'))
    with pytest.raises(ApiFailureError, match="stream failed"):
        resource_for(transport_handle).next(OWNER, SCOPE)
