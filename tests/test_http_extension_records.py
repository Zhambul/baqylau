# Copyright (c) 2026 Zhambyl Yermagambet
"""Read one ordered extension record page through the HTTP route."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from baqylau_extension_api.models import documents, record_changes, records, scopes

from domain.ids import SessionId
from repository.contract.session_data import SessionDataChanges
from repository.impl.sqlite import databases

if TYPE_CHECKING:
    from pathlib import Path


from tests import (
    http_test_assets,
    http_test_controls,
    http_test_server_runtime,
    sqlite_migration_events as events,
    sqlite_test_dependencies as test_dependencies,
)

OWNER = "test.owner"
COLLECTION = "test.owner.notes"
SCOPE = scopes.SessionScope.model_validate_json(
    '{"kind":"session","session_id":"session-one","actor_id":"lead","harness":"codex"}',
)
BASE_KEY = records.RecordKey(
    owner=OWNER, collection=COLLECTION, scope=SCOPE, key="note-0",
)
DIGEST_LENGTH = 64
DIGEST = "a" * DIGEST_LENGTH
SCHEMA_REF = documents.SchemaRef(owner=OWNER, name="text", version=1, digest=DIGEST)
DOCUMENT = documents.EncodedDocument(schema_ref=SCHEMA_REF, json_text='"first"')
FIRST_CURSOR = 7
SUMMARY = "First note"
SESSION = SessionId("session-one")
RECORD_COUNT = 3
DATA_DIRECTORY = "data"
DATABASE_NAME = "main.db"


def seed_records(tmp_path: Path) -> None:
    """Commit three owned records into the private application database."""
    database = databases.main_database(str(tmp_path / DATA_DIRECTORY / DATABASE_NAME))
    events.populate(database)
    store = test_dependencies.SqliteSessionDataRepository(database)
    for index in range(RECORD_COUNT):
        key = BASE_KEY.model_copy(update={"key": f"note-{index}"})
        change = record_changes.PutRecord(
            key=key, expected_revision=0, document=DOCUMENT, summary=SUMMARY,
        )
        store.apply(SESSION, SessionDataChanges(records=(change,)), FIRST_CURSOR + index)


def test_record_route_pages_by_key(tmp_path: Path) -> None:
    """The record route returns ordered rows and the continuation key."""
    seed_records(tmp_path)
    query = urlencode({"scope": SCOPE.model_dump_json(), "limit": 2})

    with http_test_assets.running_server(http_test_server_runtime.application()) as server:
        response = http_test_controls.get(server, f"/api/extensions/{OWNER}/records/{COLLECTION}?{query}")

    document = response[2].json
    assert response[0] == HTTPStatus.OK
    record_keys = [record["key"]["key"] for record in document["records"]]
    assert record_keys == ["note-0", "note-1"]
    assert all(record["state"] == "stored" for record in document["records"])
    assert document["next_key"] == "note-1"


def test_record_route_rejects_an_invalid_scope(tmp_path: Path) -> None:
    """A scope document the host cannot decode is a client error."""
    seed_records(tmp_path)
    query = urlencode({"scope": "not-json"})

    with http_test_assets.running_server(http_test_server_runtime.application()) as server:
        response = http_test_controls.get(server, f"/api/extensions/{OWNER}/records/{COLLECTION}?{query}")

    assert response[0] == HTTPStatus.BAD_REQUEST
