# Copyright (c) 2026 Zhambyl Yermagambet
"""Round-trip one extension entry through storage and the API mapper."""

from __future__ import annotations

from api.sessiondata import entry_body_mapper
from api.sessiondata.models.entry_extension_bodies import ExtensionBodyResponse
from domain import entries, entry_extensions
from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
)

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
ACTOR = domain_dependencies.domain_ids.ActorId("lead")
ENTRY_ID = domain_dependencies.domain_ids.CanonicalEventId("event-extension")
SOURCE_ID = domain_dependencies.domain_ids.CanonicalEventId("fact-extension")
CURSOR = 5
DIGEST_LENGTH = 64
DIGEST = "a" * DIGEST_LENGTH
OWNER = "test.owner"
ENTRY_TYPE = "test.owner.card"
SCHEMA_REF = entry_extensions.ExtensionSchemaIdentity(owner=OWNER, name="text", version=1, digest=DIGEST)
BODY = entry_extensions.ExtensionEntryBody(
    owner=OWNER, entry_type=ENTRY_TYPE, source_event_id=SOURCE_ID, schema_ref=SCHEMA_REF, document='"card"',
)
ENTRY = entries.SessionEntry(
    entry_id=ENTRY_ID,
    session_id=SESSION,
    actor_id=ACTOR,
    parent_actor_id=None,
    turn_id=None,
    occurred_at=1.0,
    summary="Card",
    body=BODY,
)


def test_extension_entry_round_trips(main: repository_dependencies.SqliteDatabase) -> None:
    """An extension entry stores, reads back exactly, and maps to its API response."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    store.apply(SESSION, repository_dependencies.SessionDataChanges(entries=(ENTRY,)), CURSOR)

    stored = store.entries_page(SESSION, limit=10).entries[0]
    assert stored.body == BODY
    assert stored.entry_type == entries.EntryTypeName.EXTENSION

    response = entry_body_mapper.entry_body(stored.body)
    assert isinstance(response, ExtensionBodyResponse)
    assert (response.owner, response.entry_type, response.document) == (OWNER, ENTRY_TYPE, '"card"')
    assert response.schema_ref.digest == DIGEST
