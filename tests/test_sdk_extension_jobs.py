# Copyright (c) 2026 Zhambyl Yermagambet
"""Read durable extension jobs through the typed SDK resource."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlencode

from baqylau_extension_api.models.scopes import ExtensionScope
from pydantic import TypeAdapter

from api.extensions.job_models import ExtensionJobCancelRequest, ExtensionJobReconcileRequest
from tests.sdk_test_resources import extensions_resource
from tests.sdk_test_support import _transport_response

if TYPE_CHECKING:
    from pydantic import BaseModel

OWNER = "test.owner"
JOB_ID = "job-1"
SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)
SCOPE = SCOPE_ADAPTER.validate_json('{"kind":"installation"}')
FIRST_REVISION = 1
SECOND_REVISION = 2

type TransportPost = tuple[str, BaseModel, set[int], float | None]


class JobsTransport:
    """Answer one typed job read or cancel request."""

    def __init__(self) -> None:
        """Create empty request records."""
        self.paths: list[str] = []
        self.posts: list[TransportPost] = []

    def get[Response](self, path: str, adapter: TypeAdapter[Response]) -> Response:
        """Record the path and answer one job.

        Returns:
            The typed job response.

        """
        self.paths.append(path)
        return _transport_response(adapter, {
            "job_id": JOB_ID, "kind": "command", "state": "accepted", "revision": FIRST_REVISION,
        })

    def post[Response](
        self,
        path: str,
        document: BaseModel,
        adapter: TypeAdapter[Response],
        accepted_statuses: set[int],
        *,
        timeout: float | None = None,
    ) -> tuple[int, Response]:
        """Record the request and answer one cancellation.

        Returns:
            HTTP 200 and the typed cancellation response.

        """
        self.posts.append((path, document, accepted_statuses, timeout))
        if path.endswith("/reconcile"):
            return 200, _transport_response(adapter, {
                "job_id": JOB_ID, "kind": "command", "state": "succeeded", "revision": SECOND_REVISION,
            })
        return 200, _transport_response(adapter, {"status": "canceled", "revision": SECOND_REVISION})


def test_extension_jobs_read_the_exact_scope() -> None:
    """The SDK sends the exact scope as a query parameter."""
    transport = JobsTransport()
    job = extensions_resource(transport).jobs.read(OWNER, JOB_ID, SCOPE)

    query = urlencode({"scope": SCOPE.model_dump_json()})
    assert transport.paths == [f"/api/extensions/{OWNER}/jobs/{JOB_ID}?{query}"]
    assert (job.job_id, job.state, job.revision) == (JOB_ID, "accepted", FIRST_REVISION)


def test_extension_job_cancel_posts_the_exact_revision() -> None:
    """The SDK posts the expected revision to the cancel route."""
    transport = JobsTransport()
    request = ExtensionJobCancelRequest(
        scope=SCOPE.model_dump_json(), expected_revision=FIRST_REVISION, reason="stop",
    )
    response = extensions_resource(transport).jobs.cancel(OWNER, JOB_ID, request)

    posted = transport.posts[0]
    assert posted[0] == f"/api/extensions/{OWNER}/jobs/{JOB_ID}/cancel"
    assert isinstance(posted[1], ExtensionJobCancelRequest)
    assert posted[1].expected_revision == FIRST_REVISION
    assert (response.status, response.revision) == ("canceled", SECOND_REVISION)


def test_extension_job_reconcile_posts_the_exact_revision() -> None:
    """The SDK posts the expected revision to the reconcile route."""
    transport = JobsTransport()
    request = ExtensionJobReconcileRequest(
        scope=SCOPE.model_dump_json(), expected_revision=FIRST_REVISION,
    )
    job = extensions_resource(transport).jobs.reconcile(OWNER, JOB_ID, request)

    posted = transport.posts[0]
    assert posted[0] == f"/api/extensions/{OWNER}/jobs/{JOB_ID}/reconcile"
    assert isinstance(posted[1], ExtensionJobReconcileRequest)
    assert posted[1].expected_revision == FIRST_REVISION
    assert (job.job_id, job.state, job.revision) == (JOB_ID, "succeeded", SECOND_REVISION)
