# Copyright (c) 2026 Zhambyl Yermagambet
"""Read a waiting queue through the session stream and the application route."""

import json
from http.client import HTTPConnection

from engine.interpret.loop import Interpreter
from tests import http_test_assets, http_test_controls
from tests.e2e_replay.test_claude_queued_prompt import QUEUED_TEXT, REQUEST_ID, queued_message
from tests.provider_graph import ProviderGraph

APPLICATION_EVENT = "event: application\n"
FRAME_LINE_LIMIT = 30


def test_stream_and_route_send_one_queue_shape() -> None:
    """Send a waiting queue in the same shape on the session stream and the route.

    The stream sent `messages` and the route sent `items`. The browser refused
    each stream frame while the queue had a prompt in it.
    """
    application = ProviderGraph()
    application.raw_events.record((queued_message(),))
    application.provider("interpreter", Interpreter).tick()
    application.reaction_loop.tick()
    with http_test_assets.running_server(application) as server:
        route_application = json.loads(http_test_controls.get(server, "/api/sessions/session-one/application").body.raw)
        stream_application = _first_application_frame(server.server_port, "/sessionData/session-one/stream")
    queued_items = [{"request_id": REQUEST_ID, "text": QUEUED_TEXT}]
    assert route_application["composer"]["queue"]["items"] == queued_items
    assert stream_application == route_application


def _first_application_frame(port: int, path: str) -> object:
    """Read the stream lines until the first application frame ends.

    Returns:
        The decoded body of the application frame.

    """
    connection = HTTPConnection(http_test_controls.LOOPBACK_ADDRESS, port, timeout=5)
    connection.request("GET", path)
    response = connection.getresponse()
    lines: list[str] = []
    while len(lines) < FRAME_LINE_LIMIT:
        line = response.fp.readline().decode()
        if not line:
            break
        lines.append(line)
        if line == "\n" and APPLICATION_EVENT in lines:
            break
    connection.close()
    frame_body = lines[lines.index(APPLICATION_EVENT) + 1].removeprefix("data: ")
    return json.loads(frame_body)
