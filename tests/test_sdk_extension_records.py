# Copyright (c) 2026 Zhambyl Yermagambet
"""Read extension records through the typed SDK resource."""

from __future__ import annotations

from urllib.parse import urlencode

from baqylau_extension_api.models.scopes import ExtensionScope
from pydantic import TypeAdapter

from tests.sdk_test_resources import extensions_resource
from tests.sdk_test_support import _transport_response

OWNER = "test.owner"
COLLECTION = "test.owner.notes"
SCOPE_ADAPTER: TypeAdapter[ExtensionScope] = TypeAdapter(ExtensionScope)
SCOPE = SCOPE_ADAPTER.validate_json(
    '{"kind":"session","session_id":"session-one","actor_id":"lead","harness":"codex"}',
)
AFTER_KEY = "note-1"
PAGE_LIMIT = 2
EXPECTED_KEY = "note-2"


class RecordsTransport:
    """Answer one typed record page request."""

    def __init__(self) -> None:
        """Create an empty request record."""
        self.paths: list[str] = []

    def get[Response](self, path: str, adapter: TypeAdapter[Response]) -> Response:
        """Record the path and answer one record page.

        Returns:
            The typed record page response.

        """
        self.paths.append(path)
        return _transport_response(adapter, {"records": (), "next_key": EXPECTED_KEY})


def test_extension_records_query_the_exact_scope() -> None:
    """The SDK sends the scope, continuation key, and limit as query parameters."""
    transport = RecordsTransport()
    found = extensions_resource(transport).records(OWNER, COLLECTION, SCOPE, after=AFTER_KEY, limit=PAGE_LIMIT)

    query = urlencode({"scope": SCOPE.model_dump_json(), "after": AFTER_KEY, "limit": PAGE_LIMIT})
    assert transport.paths == [f"/api/extensions/{OWNER}/records/{COLLECTION}?{query}"]
    assert found.next_key == EXPECTED_KEY
