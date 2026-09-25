# Copyright (c) 2026 Zhambyl Yermagambet
"""Submit durable extension commands through the typed SDK resource."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.models.scopes import InstallationScope

from api.extensions.command_models import ExtensionCommandRequest
from tests.sdk_test_resources import extensions_resource
from tests.sdk_test_support import _transport_response

if TYPE_CHECKING:
    from pydantic import BaseModel, TypeAdapter

OWNER = "test.owner"
COMMAND_ID = "test.owner.command"
SCOPE = InstallationScope()
REQUEST_KEY = "request-1"
ARGUMENTS = '"input"'
JOB_ID = "job-1"
FIRST_REVISION = 1

type TransportPost = tuple[str, BaseModel, set[int], float | None]


class CommandTransport:
    """Answer one typed command request."""

    def __init__(self) -> None:
        """Create an empty request record."""
        self.posts: list[TransportPost] = []

    def post[Response](
        self,
        path: str,
        document: BaseModel,
        adapter: TypeAdapter[Response],
        accepted_statuses: set[int],
        *,
        timeout: float | None = None,
    ) -> tuple[int, Response]:
        """Record the request and answer one finished job.

        Returns:
            HTTP 200 and the typed job response.

        """
        self.posts.append((path, document, accepted_statuses, timeout))
        return 202, _transport_response(adapter, {
            "job_id": JOB_ID, "kind": "command", "state": "accepted", "revision": FIRST_REVISION,
        })


def test_extension_command_posts_the_request() -> None:
    """The SDK posts the exact request key and arguments."""
    transport = CommandTransport()
    request = ExtensionCommandRequest(
        scope=SCOPE.model_dump_json(), request_key=REQUEST_KEY, arguments=ARGUMENTS,
    )
    job = extensions_resource(transport).commands.submit(OWNER, COMMAND_ID, request)

    posted = transport.posts[0]
    assert posted[0] == f"/api/extensions/{OWNER}/commands/{COMMAND_ID}"
    assert isinstance(posted[1], ExtensionCommandRequest)
    assert posted[1].request_key == REQUEST_KEY
    assert (job.job_id, job.state, job.revision) == (JOB_ID, "accepted", FIRST_REVISION)
