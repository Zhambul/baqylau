# Copyright (c) 2026 Zhambyl Yermagambet
"""Run declared extension reads through the typed SDK resource."""

from __future__ import annotations

from typing import TYPE_CHECKING

from baqylau_extension_api.models.documents import EncodedDocument, SchemaRef
from baqylau_extension_api.models.operations import OperationBinding, QuerySnapshot
from baqylau_extension_api.models.scopes import InstallationScope

from api.extensions.query_models import ExtensionQueryReadyResponse, ExtensionQueryRequest
from tests.sdk_test_resources import extensions_resource
from tests.sdk_test_support import _transport_response

if TYPE_CHECKING:
    from pydantic import BaseModel, TypeAdapter

OWNER = "test.query"
QUERY_ID = "test.query.read"
ARGUMENTS = '"fixture"'
SCOPE = InstallationScope()
RUNTIME_REVISION = "runtime-one"
CALL_ID = "call-one"
DIGEST_LENGTH = 64
DIGEST = "a" * DIGEST_LENGTH
SCHEMA_REF = SchemaRef(owner=OWNER, name="text", version=1, digest=DIGEST)
BINDING = OperationBinding(
    extension_id=OWNER, operation_id=QUERY_ID, scope=SCOPE,
    runtime_revision=RUNTIME_REVISION, call_id=CALL_ID,
)
READY = ExtensionQueryReadyResponse(
    binding=BINDING,
    document=EncodedDocument(schema_ref=SCHEMA_REF, json_text=ARGUMENTS),
    snapshot=QuerySnapshot(state_revision="state-1"),
)

type TransportPost = tuple[str, BaseModel, set[int], float | None]


class QueryTransport:
    """Answer one typed query request."""

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
        """Record the request and answer one ready result.

        Returns:
            HTTP 200 and the ready query result.

        """
        self.posts.append((path, document, accepted_statuses, timeout))
        return 200, _transport_response(adapter, READY.model_dump())


def test_extension_query_posts_declared_arguments() -> None:
    """The SDK posts the exact scope, arguments, and limit for one declared read."""
    transport = QueryTransport()
    request = ExtensionQueryRequest(scope=SCOPE.model_dump_json(), arguments=ARGUMENTS)
    found = extensions_resource(transport).queries.query(OWNER, QUERY_ID, request)

    posted = transport.posts[0]
    assert posted[0] == f"/api/extensions/{OWNER}/queries/{QUERY_ID}"
    assert isinstance(posted[1], ExtensionQueryRequest)
    assert posted[1].scope == SCOPE.model_dump_json()
    assert posted[1].arguments == ARGUMENTS
    assert found.status == "ready"
