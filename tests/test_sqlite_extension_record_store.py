# Copyright (c) 2026 Zhambyl Yermagambet
"""Store extension records atomically with their commit."""

from __future__ import annotations

import pytest
from baqylau_extension_api.models import documents, record_changes, records, scopes

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_test_migrations,
)

OWNER = "test.owner"
COLLECTION = "test.owner.notes"
SCOPE = scopes.SessionScope.model_validate_json(
    '{"kind":"session","session_id":"session-one","actor_id":"lead","harness":"codex"}',
)
KEY = records.RecordKey(owner=OWNER, collection=COLLECTION, scope=SCOPE, key="note-1")
DIGEST_LENGTH = 64
DIGEST = "a" * DIGEST_LENGTH
SCHEMA_REF = documents.SchemaRef(owner=OWNER, name="text", version=1, digest=DIGEST)
FIRST_DOCUMENT = documents.EncodedDocument(schema_ref=SCHEMA_REF, json_text='"first"')
SECOND_DOCUMENT = documents.EncodedDocument(schema_ref=SCHEMA_REF, json_text='"second"')
FIRST_CURSOR = 7
SECOND_CURSOR = 8
SUMMARY = "First note"
SECOND_SUMMARY = "Second note"
SESSION = domain_dependencies.domain_ids.SessionId("session-one")
STALE_MESSAGE = "changed since it was captured"
RECORD_ENTRY_ID = "record-entry"


def put(document: documents.EncodedDocument, expected_revision: int, summary: str) -> record_changes.PutRecord:
    """Build one put at the selected expected revision.

    Returns:
        The complete put change.

    """
    return record_changes.PutRecord(
        key=KEY, expected_revision=expected_revision, document=document, summary=summary,
    )


def only_state(main: repository_dependencies.SqliteDatabase) -> records.RecordState:
    """Read the single captured state of the fixture key.

    Returns:
        The captured state.

    """
    reader = repository_dependencies.SqliteExtensionRecordRepository(main)
    states = reader.record_states((KEY,))
    return states[0]


def entry_ids(store: test_dependencies.SqliteSessionDataRepository) -> list[str]:
    """Read the feed entry identities of the fixture session.

    Returns:
        The stored entry identities, oldest first.

    """
    return [entry.entry_id for entry in store.entries_page(SESSION, limit=10).entries]


def test_put_and_read_back(main: repository_dependencies.SqliteDatabase) -> None:
    """A put stores the exact document and the host commit revision."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    store.apply(
        SESSION,
        repository_dependencies.SessionDataChanges(records=(put(FIRST_DOCUMENT, 0, SUMMARY),)),
        FIRST_CURSOR,
    )

    state = only_state(main)
    assert isinstance(state, records.StoredRecord)
    assert (state.revision, state.document, state.summary) == (FIRST_CURSOR, FIRST_DOCUMENT, SUMMARY)


def test_stale_revision_rejects_commit(main: repository_dependencies.SqliteDatabase) -> None:
    """A capture whose key changed cannot overwrite the newer row."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    store.apply(
        SESSION,
        repository_dependencies.SessionDataChanges(records=(put(FIRST_DOCUMENT, 0, SUMMARY),)),
        FIRST_CURSOR,
    )

    with pytest.raises(ValueError, match=STALE_MESSAGE):
        store.apply(
            SESSION,
            repository_dependencies.SessionDataChanges(records=(put(SECOND_DOCUMENT, 0, SECOND_SUMMARY),)),
            SECOND_CURSOR,
        )

    state = only_state(main)
    assert isinstance(state, records.StoredRecord)
    assert state.document == FIRST_DOCUMENT


def test_delete_keeps_the_last_revision(main: repository_dependencies.SqliteDatabase) -> None:
    """A delete marks the row and keeps its schema and commit revision."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    store.apply(
        SESSION,
        repository_dependencies.SessionDataChanges(records=(put(FIRST_DOCUMENT, 0, SUMMARY),)),
        FIRST_CURSOR,
    )
    store.apply(
        SESSION,
        repository_dependencies.SessionDataChanges(records=(
            record_changes.DeleteRecord(key=KEY, expected_revision=FIRST_CURSOR),
        )),
        SECOND_CURSOR,
    )

    state = only_state(main)
    assert isinstance(state, records.DeletedRecord)
    assert (state.revision, state.schema_ref) == (SECOND_CURSOR, SCHEMA_REF)


def test_record_and_entry_commit_together(main: repository_dependencies.SqliteDatabase) -> None:
    """A record change and its entries commit or roll back as one transaction."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    store.apply(
        SESSION,
        repository_dependencies.SessionDataChanges(
            entries=(sqlite_test_migrations.an_entry(RECORD_ENTRY_ID),),
            records=(put(FIRST_DOCUMENT, 0, SUMMARY),),
        ),
        FIRST_CURSOR,
    )
    assert entry_ids(store) == [RECORD_ENTRY_ID]

    with pytest.raises(ValueError, match=STALE_MESSAGE):
        store.apply(
            SESSION,
            repository_dependencies.SessionDataChanges(
                entries=(sqlite_test_migrations.an_entry("stale-entry"),),
                records=(put(SECOND_DOCUMENT, 0, SECOND_SUMMARY),),
            ),
            SECOND_CURSOR,
        )

    assert entry_ids(store) == [RECORD_ENTRY_ID]
