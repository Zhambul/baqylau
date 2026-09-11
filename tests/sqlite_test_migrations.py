# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide sqlite test migrations."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_library_dependencies as library_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_value_dependencies as standard_dependencies,
)
from tests.harness_names import CODEX_HARNESS

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
ACTOR = domain_dependencies.domain_ids.ActorId("actor-one")
HARNESS = CODEX_HARNESS
LEAD_ACTOR_ID_TEXT = "lead"
AN_ACTOR = standard_dependencies.actor_state.ActorFacts(
    session_id=SESSION,
    actor_id=domain_dependencies.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
    role=domain_dependencies.messaging.ActorRole.LEAD,
    name="claude",
    state=domain_dependencies.lifecycle.LifecycleState.RUNNING,
)


def session_columns(database: repository_dependencies.SqliteDatabase) -> set[str]:
    """Read the session table column names.

    Returns:
        The column names in the current schema.

    """
    with database.read() as connection:
        return {row["name"] for row in connection.execute("PRAGMA table_info(sessions)")}


def raw_event_ids(*raw_event_texts: str) -> set[domain_dependencies.domain_ids.RawEventId]:
    """Convert test strings to raw event identifiers.

    Returns:
        The distinct typed identifiers.

    """
    return {domain_dependencies.domain_ids.RawEventId(raw_event_text) for raw_event_text in raw_event_texts}


def assert_canonical_pages(
    canonical: repository_dependencies.SqliteCanonicalEventRepository,
    other: domain_dependencies.domain_ids.SessionId,
) -> None:
    """Check event order and cursor boundaries for the test pages."""
    whole = canonical.page_from(0, 10)
    assert ([committed.event_id for committed in whole], [committed.session_id for committed in whole]) == (
        [
            domain_dependencies.domain_ids.CanonicalEventId("event-0"),
            domain_dependencies.domain_ids.CanonicalEventId("event-1"),
            domain_dependencies.domain_ids.CanonicalEventId("event-2"),
        ],
        [SESSION, other, SESSION],
    )
    cursors = [committed.cursor for committed in whole]
    assert None not in cursors
    stored_cursors = [cursor for cursor in cursors if cursor is not None]
    assert stored_cursors == sorted(stored_cursors)
    assert [committed.event_id for committed in canonical.page_from(stored_cursors[0], 10)] == [
        domain_dependencies.domain_ids.CanonicalEventId("event-1"),
        domain_dependencies.domain_ids.CanonicalEventId("event-2"),
    ]
    first_page = canonical.page_from(0, 2)
    assert [committed.cursor for committed in first_page] == stored_cursors[:2]


def a_following(
    until: repository_dependencies.work_state.ShellFollowUntil = (
        repository_dependencies.work_state.ShellFollowUntil.SHELL_FINISHED
    ),
) -> domain_dependencies.shell_models.ShellOutputFollowing:
    """Create an active shell output following record.

    Returns:
        The test record with the requested stop condition.

    """
    return domain_dependencies.shell_models.ShellOutputFollowing(
        session_id=SESSION,
        shell_id=domain_dependencies.domain_ids.ShellId("op-one"),
        harness=HARNESS,
        actor_id=ACTOR,
        parent_actor_id=None,
        source_path="/test-data/output",
        chunk_source_type="chunk",
        delete_source=True,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=until,
        state=domain_dependencies.shell_models.ShellFollowState.ACTIVE,
        created_at=1000.0,
    )


def an_entry(entry_id: str) -> library_dependencies.domain_entries.SessionEntry:
    """Create a user prompt entry for the test session.

    Returns:
        The entry with the supplied identifier.

    """
    return library_dependencies.domain_entries.SessionEntry(
        entry_id=domain_dependencies.domain_ids.CanonicalEventId(entry_id),
        session_id=SESSION,
        actor_id=domain_dependencies.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
        parent_actor_id=None,
        turn_id=None,
        occurred_at=1.0,
        summary=None,
        body=library_dependencies.entry_conversation.MessageBody(
            domain_dependencies.domain_ids.MessageId(entry_id),
            domain_dependencies.messaging.MessageRole.USER,
            domain_dependencies.messaging.MessagePhase.PROMPT,
            library_dependencies.domain_content.TextContent("go"),
        ),
    )


def entry_ids_at(store: test_dependencies.SqliteSessionDataRepository, cursor: int) -> list[str]:
    """Read entry identifiers at a session cursor.

    Returns:
        The identifiers in the first page at the cursor.

    """
    page = store.entries_page(SESSION, at=cursor, limit=10)
    return [entry.entry_id for entry in page.entries]


def working_actor() -> standard_dependencies.actor_state.ActorFacts:
    """Set the test actor status to working.

    Returns:
        A copy of the actor with working status.

    """
    return standard_dependencies.replace(AN_ACTOR, status=standard_dependencies.actor_state.ActorStatus.WORKING)
