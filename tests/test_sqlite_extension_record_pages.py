# Copyright (c) 2026 Zhambyl Yermagambet
"""Page extension record rows in their exact scope."""

from __future__ import annotations

from baqylau_extension_api.models import documents, record_changes, records, scopes

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
)

OWNER = "test.owner"
COLLECTION = "test.owner.notes"
SCOPE_JSON = '{"kind":"session","session_id":"session-one","actor_id":"lead","harness":"codex"}'
SCOPE = scopes.SessionScope.model_validate_json(SCOPE_JSON)
BASE_KEY = records.RecordKey(
    owner=OWNER, collection=COLLECTION, scope=SCOPE, key="note-0",
)
DIGEST_LENGTH = 64
DIGEST = "a" * DIGEST_LENGTH
SCHEMA_REF = documents.SchemaRef(owner=OWNER, name="text", version=1, digest=DIGEST)
DOCUMENT = documents.EncodedDocument(schema_ref=SCHEMA_REF, json_text='"first"')
FIRST_CURSOR = 7
SUMMARY = "First note"
SESSION = domain_dependencies.domain_ids.SessionId("session-one")
PAGE_SIZE = 2
RECORD_COUNT = 3


def seed_records(store: test_dependencies.SqliteSessionDataRepository) -> None:
    """Commit three owned records at consecutive cursors."""
    for index in range(RECORD_COUNT):
        key = BASE_KEY.model_copy(update={"key": f"note-{index}"})
        change = record_changes.PutRecord(
            key=key, expected_revision=0, document=DOCUMENT, summary=SUMMARY,
        )
        store.apply(
            SESSION,
            repository_dependencies.SessionDataChanges(records=(change,)),
            FIRST_CURSOR + index,
        )


def test_record_page_continues_by_key(main: repository_dependencies.SqliteDatabase) -> None:
    """A record page returns ordered states and one continuation key."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    seed_records(store)

    reader = repository_dependencies.SqliteExtensionRecordRepository(main)
    page = reader.record_page(OWNER, COLLECTION, SCOPE, "", PAGE_SIZE)
    assert [state.key.key for state in page.records] == ["note-0", "note-1"]
    assert page.next_key == "note-1"

    following = reader.record_page(OWNER, COLLECTION, SCOPE, page.next_key, PAGE_SIZE)
    assert [state.key.key for state in following.records] == ["note-2"]
    assert following.next_key is None
