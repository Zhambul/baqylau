# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide sqlite test events."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_library_dependencies as library_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_value_dependencies as standard_dependencies,
)
from tests.harness_names import CODEX_HARNESS

# Keep dependency modules separate from test helpers.
# isort: split

from tests import (
    sqlite_test_fixtures,
    sqlite_test_migrations,
    sqlite_test_models,
    sqlite_test_shells,
)

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
SESSION_TEXT = str(SESSION)
ACTOR = domain_dependencies.domain_ids.ActorId("actor-one")
HARNESS = CODEX_HARNESS
FIRST_TRANSLATION_TIME = 1001.0
SECOND_TRANSLATION_TIME = 1002.0
THIRD_TRANSLATION_TIME = 1003.0
FOURTH_TRANSLATION_TIME = 1004.0
FIRST_HARNESS_PROCESS_ID = 101
RESUMED_HARNESS_PROCESS_ID = 202
FIRST_SOURCE_POSITION = "1"
SECOND_SOURCE_POSITION = "2"
THIRD_SOURCE_POSITION = "3"
LEAD_ACTOR_ID_TEXT = "lead"
FIRST_ENTRY_ID = "e1"
AN_ACTOR = standard_dependencies.actor_state.ActorFacts(
    session_id=SESSION,
    actor_id=domain_dependencies.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
    role=domain_dependencies.messaging.ActorRole.LEAD,
    name="claude",
    state=domain_dependencies.lifecycle.LifecycleState.RUNNING,
)


class ResumedRunScenario:
    """Build and record the facts for a resumed run migration."""

    def __init__(self) -> None:
        """Build start and exit events for the original and resumed runs."""
        self.first_window = domain_dependencies.domain_ids.WindowId("window-one")
        self.resumed_window = domain_dependencies.domain_ids.WindowId("window-two")
        first_started_raw = sqlite_test_fixtures.a_raw_event("first-started")
        first_exit = standard_dependencies.replace(
            sqlite_test_fixtures.a_raw_event("first-exit"),
            source_type="liveness",
            terminal_window_id=self.first_window,
        )
        resumed_started_raw = sqlite_test_fixtures.a_raw_event("resumed-started")
        resumed_exit = standard_dependencies.replace(
            sqlite_test_fixtures.a_raw_event("resumed-exit"),
            source_type="liveness",
            terminal_window_id=self.resumed_window,
        )
        shared_finish = standard_dependencies.replace(
            sqlite_test_fixtures.a_started_event("old-shared-session-finish"),
            terminal_window_id=self.first_window,
            harness_process_id=FIRST_HARNESS_PROCESS_ID,
            payload=library_dependencies.event_session.SessionFinished(
                domain_dependencies.outcomes.Outcome.UNKNOWN,
                "process_exited",
            ),
        )
        self.raw_events = (first_started_raw, first_exit, resumed_started_raw, resumed_exit)
        self.translations = (
            (
                first_started_raw,
                standard_dependencies.replace(
                    sqlite_test_fixtures.a_started_event("first-run-started"),
                    terminal_window_id=self.first_window,
                    harness_process_id=FIRST_HARNESS_PROCESS_ID,
                ),
                FIRST_TRANSLATION_TIME,
            ),
            (first_exit, shared_finish, SECOND_TRANSLATION_TIME),
            (
                resumed_started_raw,
                standard_dependencies.replace(
                    sqlite_test_fixtures.a_started_event("resumed-run-started"),
                    terminal_window_id=self.resumed_window,
                    harness_process_id=RESUMED_HARNESS_PROCESS_ID,
                ),
                THIRD_TRANSLATION_TIME,
            ),
            (resumed_exit, shared_finish, FOURTH_TRANSLATION_TIME),
        )

    def record(self, database: repository_dependencies.SqliteDatabase) -> None:
        """Record the source database facts."""
        test_dependencies.SqliteSessionRepository(database).save(
            HARNESS,
            sqlite_test_fixtures.a_session(self.first_window, FIRST_HARNESS_PROCESS_ID),
        )
        test_dependencies.SqliteRawEventRepository(database).record(self.raw_events)
        canonical = repository_dependencies.SqliteCanonicalEventRepository(database)
        for raw_event, event, translation_time in self.translations:
            canonical.record_translation(
                raw_event,
                FIRST_SOURCE_POSITION,
                repository_dependencies.raw_event_models.TranslationResult(
                    (event,),
                    domain_dependencies.domain_records.RecordedTranslationDecision.TRANSLATED,
                ),
                translation_time,
            )


def legacy_model_event_pairs() -> tuple[tuple[repository_dependencies.raw_event_models.RawEvent, str], ...]:
    """Build raw events and their legacy model event identifiers.

    Returns:
        Raw event and canonical event identifier pairs.

    """
    return (
        (sqlite_test_fixtures.a_raw_event("legacy-model", FIRST_SOURCE_POSITION), "legacy-model-event"),
        (sqlite_test_fixtures.a_raw_event("legacy-context", SECOND_SOURCE_POSITION), "legacy-context-event"),
        (sqlite_test_fixtures.a_raw_event("legacy-usage", THIRD_SOURCE_POSITION), "legacy-usage-event"),
    )


def record_legacy_model_events(
    database: repository_dependencies.SqliteDatabase,
    event_pairs: tuple[tuple[repository_dependencies.raw_event_models.RawEvent, str], ...],
) -> None:
    """Record source events and initial facts for a legacy model migration."""
    test_dependencies.SqliteRawEventRepository(database).record([event_pair[0] for event_pair in event_pairs])
    canonical = repository_dependencies.SqliteCanonicalEventRepository(database)
    for raw_event, event_id in event_pairs:
        canonical.record_translation(
            raw_event,
            FIRST_SOURCE_POSITION,
            repository_dependencies.raw_event_models.TranslationResult(
                (sqlite_test_fixtures.a_started_event(event_id),),
                domain_dependencies.domain_records.RecordedTranslationDecision.TRANSLATED,
            ),
            FIRST_TRANSLATION_TIME,
        )


def record_session_fact(
    database: repository_dependencies.SqliteDatabase,
    raw_event_id: str,
    source_position: str,
    event: library_dependencies.event_base.CanonicalEvent[library_dependencies.event_base.EventPayload],
    translation_time: float,
) -> None:
    """Record one source event and its canonical session fact."""
    raw_event = sqlite_test_fixtures.a_raw_event(raw_event_id, source_position)
    test_dependencies.SqliteRawEventRepository(database).record([raw_event])
    repository_dependencies.SqliteCanonicalEventRepository(database).record_translation(
        raw_event,
        FIRST_SOURCE_POSITION,
        repository_dependencies.raw_event_models.TranslationResult(
            (event,),
            domain_dependencies.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
        translation_time,
    )


def record_paged_event(
    database: repository_dependencies.SqliteDatabase,
    index: int,
    session_id: domain_dependencies.domain_ids.SessionId,
) -> None:
    """Record an indexed user message for a pagination test."""
    raw_event = sqlite_test_fixtures.a_raw_event(f"raw-{index}", str(index))
    test_dependencies.SqliteRawEventRepository(database).record([raw_event])
    repository_dependencies.SqliteCanonicalEventRepository(database).record_translation(
        raw_event,
        FIRST_SOURCE_POSITION,
        repository_dependencies.raw_event_models.TranslationResult(
            (
                library_dependencies.event_base.CanonicalEvent(
                    event_id=domain_dependencies.domain_ids.CanonicalEventId(f"event-{index}"),
                    session_id=session_id,
                    actor_id=ACTOR,
                    turn_id=None,
                    parent_actor_id=None,
                    harness=HARNESS,
                    occurred_at=1000.0 + index,
                    terminal_window_id=None,
                    harness_process_id=None,
                    payload=library_dependencies.event_conversation.MessageCreated(
                        domain_dependencies.domain_ids.MessageId(f"m{index}"),
                        domain_dependencies.messaging.MessageRole.USER,
                        library_dependencies.domain_content.TextContent("hi"),
                        domain_dependencies.messaging.MessagePhase.PROMPT,
                        None,
                    ),
                ),
            ),
            domain_dependencies.domain_records.RecordedTranslationDecision.TRANSLATED,
        ),
        1000.0 + index,
    )


def first_entry_change() -> repository_dependencies.SessionDataChanges:
    """Build the first fixture entry as one read-model change.

    Returns:
        The first fixture entry as one read-model change.

    """
    return repository_dependencies.SessionDataChanges(entries=(sqlite_test_migrations.an_entry(FIRST_ENTRY_ID),))


def store_version_four_actor(
    migration: sqlite_test_models.MigrationDatabase,
) -> standard_dependencies.actor_state.ActorFacts:
    """Store an actor with the version four model fields.

    Returns:
        The actor before conversion to the legacy payload.

    """
    actor = standard_dependencies.replace(
        AN_ACTOR,
        model=domain_dependencies.references.ModelReference(name="claude-opus-5", display_name="opus-5"),
    )
    actor_document = test_dependencies.documents.encode_document(actor).decode()
    with migration.old.write() as connection:
        connection.execute(
            (
                "INSERT INTO session_data_actors(session_id, actor_id, revision, payload)\n  "
                "             VALUES (?, ?, ?, ?)"
            ),
            (SESSION_TEXT, str(actor.actor_id), 1, actor_document),
        )
        connection.execute(
            (
                "UPDATE session_data_actors\n               SET payload = json_set(\n         "
                "          json_remove(payload, '$.model.name'),\n                   "
                "'$.model.native_id', json_extract(payload, '$.model.name'),\n               "
                "    '$.model.selection_id', 'opus'\n               )"
            ),
        )
        sqlite_test_fixtures.restore_version_six_queue_table(connection)
        sqlite_test_shells.restore_version_ten_schema(connection)
        connection.execute("UPDATE schema_version SET version = 4 WHERE id = 1")
    return actor
