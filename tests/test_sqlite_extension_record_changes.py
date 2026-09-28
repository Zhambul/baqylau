# Copyright (c) 2026 Zhambyl Yermagambet
"""Read extension record changes one committed boundary at a time."""

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
DIGEST_LENGTH = 64
DIGEST = "a" * DIGEST_LENGTH
SCHEMA_REF = documents.SchemaRef(owner=OWNER, name="text", version=1, digest=DIGEST)
DOCUMENT = documents.EncodedDocument(schema_ref=SCHEMA_REF, json_text='"first"')
SUMMARY = "First note"
SESSION = domain_dependencies.domain_ids.SessionId("session-one")
FIRST_CURSOR = 7
RECORD_COUNT = 3
DEFAULT_GENERATION = "default"
OTHER_GENERATION = "other"
REPOSITORY_SCOPE = scopes.RepositoryScope(
    repository_id="repo-one", worktree="/data/repo", git_directory="/data/repo/.git",
)
LIVE_GENERATION = "default"
FIRST_NOTE = "note-0"


def put_one(store: test_dependencies.SqliteSessionDataRepository, index: int, cursor: int) -> None:
    """Commit one owned record at the supplied cursor."""
    key = records.RecordKey(owner=OWNER, collection=COLLECTION, scope=SCOPE, key=f"note-{index}")
    change = record_changes.PutRecord(key=key, expected_revision=0, document=DOCUMENT, summary=SUMMARY)
    store.apply(SESSION, repository_dependencies.SessionDataChanges(records=(change,)), cursor)


def seed_records(store: test_dependencies.SqliteSessionDataRepository) -> None:
    """Commit three owned records at consecutive cursors."""
    for index in range(RECORD_COUNT):
        put_one(store, index, FIRST_CURSOR + index)


def read_boundary(
    reader: repository_dependencies.SqliteExtensionRecordRepository, after_cursor: int,
) -> tuple[list[str], int]:
    """Read one change boundary of the test scope.

    Returns:
        The changed record keys and the next cursor.

    """
    page = reader.record_changes(OWNER, SCOPE, DEFAULT_GENERATION, after_cursor)
    return [state.key.key for state in page.changes], page.next_cursor


def test_change_read_returns_one_boundary(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A change read returns one whole boundary and advances once."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    seed_records(store)
    reader = repository_dependencies.SqliteExtensionRecordRepository(main)

    boundaries = [read_boundary(reader, 0)]
    for _ in range(RECORD_COUNT):
        boundaries.append(read_boundary(reader, boundaries[-1][1]))

    last_cursor = FIRST_CURSOR + RECORD_COUNT - 1
    assert boundaries == [
        ([FIRST_NOTE], FIRST_CURSOR),
        (["note-1"], FIRST_CURSOR + 1),
        (["note-2"], last_cursor),
        ([], last_cursor),
    ]


def test_change_read_scopes_to_the_generation(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A change read ignores rows from another projection generation."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    seed_records(store)
    reader = repository_dependencies.SqliteExtensionRecordRepository(main)

    other = reader.record_changes(OWNER, SCOPE, OTHER_GENERATION, 0)
    assert other.changes == ()
    assert other.next_cursor == 0


def test_change_read_resumes_across_a_later_write(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A write between reads appears at its own later boundary."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    put_one(store, 0, FIRST_CURSOR)
    reader = repository_dependencies.SqliteExtensionRecordRepository(main)

    first = reader.record_changes(OWNER, SCOPE, DEFAULT_GENERATION, 0)
    assert [state.key.key for state in first.changes] == [FIRST_NOTE]

    put_one(store, 1, FIRST_CURSOR + 1)
    second = reader.record_changes(OWNER, SCOPE, DEFAULT_GENERATION, first.next_cursor)
    assert [state.key.key for state in second.changes] == ["note-1"]
    assert second.next_cursor == FIRST_CURSOR + 1


def test_change_read_works_in_a_repository_scope(
    main: repository_dependencies.SqliteDatabase,
) -> None:
    """A repository-scope record changes without any session."""
    store = repository_dependencies.SqliteExtensionProjectionRepository(main)
    key = records.RecordKey(owner=OWNER, collection=COLLECTION, scope=REPOSITORY_SCOPE, key=FIRST_NOTE)
    change = record_changes.PutRecord(
        key=key, expected_revision=0, document=DOCUMENT, summary=SUMMARY,
    )
    store.apply_projection(repository_dependencies.ProjectionCommit(
        owner=OWNER,
        scope=REPOSITORY_SCOPE,
        history_revision="default",
        generation=LIVE_GENERATION,
        commit_cursor=FIRST_CURSOR,
        changes=repository_dependencies.SessionDataChanges(records=(change,)),
    ))

    reader = repository_dependencies.SqliteExtensionRecordRepository(main)
    page = reader.record_changes(OWNER, REPOSITORY_SCOPE, LIVE_GENERATION, 0)
    assert [state.key.key for state in page.changes] == [FIRST_NOTE]
    assert page.next_cursor == FIRST_CURSOR
