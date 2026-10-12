# Copyright (c) 2026 Zhambyl Yermagambet
"""A pane asks for an event stream, so the daemon sends the stream's headers at once.

Without the accept type, the compression middleware held the headers back
until the first frame. A quiet session's pane then waited up to its stall time
for a status line, and a busy daemon pushed it past that time.
"""

from __future__ import annotations

from http import HTTPStatus

from tests import test_client_loading

exchange = test_client_loading.load_shared("_daemon_exchange")


class _Response:
    status = HTTPStatus.OK

    def __iter__(self) -> _Response:
        return self

    def __next__(self) -> bytes:
        raise StopIteration


class _RecordingConnection:
    def __init__(self) -> None:
        self.headers: object = None

    def request(self, _method: str, _path: str, headers: object = None) -> None:
        self.headers = headers

    def getresponse(self) -> _Response:
        return _Response()


def test_stream_request_asks_for_an_event_stream() -> None:
    """The stream request names the event-stream type."""
    recording = _RecordingConnection()

    assert list(exchange.stream_lines(recording, "/sessionData/one/stream")) == []

    assert recording.headers == {"Accept": "text/event-stream"}
