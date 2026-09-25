# Copyright (c) 2026 Zhambyl Yermagambet
"""The kit reads records, query results, and jobs with the public SDK's wire models (P08-T01)."""

from __future__ import annotations

from http import HTTPStatus

import httpx
from baqylau_extension_api.models.documents import EncodedDocument, SchemaRef
from baqylau_extension_api.models.operations import OperationBinding, QuerySnapshot
from baqylau_extension_api.models.queries import QueryReady
from baqylau_extension_api.models.scopes import InstallationScope
from baqylau_extension_testkit import data_reads
from baqylau_extension_testkit.client import HostClient
from baqylau_extension_testkit.data_models import CommandRequest, QueryRequest

OWNER = "test.kit"
COLLECTION = f"{OWNER}.notes"
QUERY_ID = f"{OWNER}.read"
SCOPE = InstallationScope().model_dump_json()
ARGUMENTS = '"x"'
DIGEST_LENGTH = 64
DIGEST = "a" * DIGEST_LENGTH
SCHEMA = SchemaRef(owner=OWNER, name="text", version=1, digest=DIGEST)
BINDING = OperationBinding(
    extension_id=OWNER, operation_id=QUERY_ID, scope=InstallationScope(), runtime_revision="runtime-one",
    call_id="call-one",
)
READY = QueryReady(
    binding=BINDING, document=EncodedDocument(schema_ref=SCHEMA, json_text=ARGUMENTS),
    snapshot=QuerySnapshot(state_revision="state-1"),
)


class RecordedHost:
    """Answer each request with the next reply and keep the requests."""

    def __init__(self, *replies: httpx.Response) -> None:
        """Keep the replies in order."""
        self.replies = iter(replies)
        self.requests: list[httpx.Request] = []

    def answer(self, request: httpx.Request) -> httpx.Response:
        """Record the request and give the next reply.

        Returns:
            The next reply.

        """
        self.requests.append(request)
        return next(self.replies)

    def client(self) -> HostClient:
        """Give a kit client over this host.

        Returns:
            The client.

        """
        return HostClient("http://host.test", httpx.MockTransport(self.answer))


def ok(document: object) -> httpx.Response:
    """Answer one JSON document.

    Returns:
        The reply.

    """
    return httpx.Response(HTTPStatus.OK, json=document)


def job(state: str) -> httpx.Response:
    """Answer one job document.

    Returns:
        The reply.

    """
    return ok({"job_id": "job-1", "kind": "command", "state": state, "revision": 1})


def test_records_select_scope_and_page() -> None:
    """The record read names the collection, scope, and continuation key, and reads the page."""
    host = RecordedHost(ok({"records": [], "next_key": "k2"}))

    page = data_reads.records(host.client(), OWNER, COLLECTION, SCOPE, after="k1")

    sent = host.requests[0].url
    assert page.next_key == "k2"
    assert sent.path == f"/api/extensions/{OWNER}/records/{COLLECTION}"
    assert (sent.params["scope"], sent.params["after"]) == (SCOPE, "k1")


def test_query_gives_the_sdk_result() -> None:
    """A ready query is the SDK's own ready model."""
    host = RecordedHost(ok(READY.model_dump(mode="json")))
    request = QueryRequest(scope=SCOPE, arguments=ARGUMENTS)

    assert data_reads.query(host.client(), OWNER, QUERY_ID, request) == READY


def test_command_waits_for_a_final_job() -> None:
    """A command's accepted job is read until its state is final."""
    host = RecordedHost(job("accepted"), job("running"), job("succeeded"))
    request = CommandRequest(scope=SCOPE, request_key="key-1", arguments=ARGUMENTS)

    final = data_reads.command(host.client(), OWNER, f"{OWNER}.write", request)

    assert final.state == "succeeded"
    assert [sent.method for sent in host.requests] == ["POST", "GET", "GET"]
