"""The storage layer on its own: every repository against a real database.

These tests build repositories directly, with no application graph and no
daemon, which is the whole point of the contract — a store that can only be
exercised through the thing that composes it is a store nobody tests.
"""

from __future__ import annotations

import time
from dataclasses import replace
from decimal import Decimal

import pytest

from audit.models import (
    ApplicationErrorRecord,
    SpawnRecord,
    StateFileRecord,
    StreamOpened,
)
from domain.entries import MessageBody, SessionEntry, ShellStartedBody
from domain.events import CanonicalEvent, MessageCreated, SessionFinished, SessionStarted
from domain.ids import (
    ActorId,
    AttentionId,
    CanonicalEventId,
    HarnessName,
    MessageId,
    ShellId,
    RawEventId,
    SessionId,
    TaskId,
    WindowId,
)
from domain.sessiondata import ActorFacts, ActorStatus, LifecycleState, SessionFacts
from domain.shells import ShellFollowState, ShellOutputFollowing
from domain.preferences import (
    NewSessionDraft,
    NewSessionPreferences,
    PushSigningKeypair,
    PushSubscription,
)
from domain.uploads import StoredUpload
from domain.values import (
    ActorRole,
    ExecutionMode,
    MessagePhase,
    MessageRole,
    Outcome,
    ShellFollowUntil,
    TextContent,
)
from domain.workspace import AnswerSelection, ComposerDraft, ComposerQueue, DialogDraft, QueuedMessage
from harness.models import AccountUsageSnapshot, RawEvent, Session, TranslationResult, UsageWindowSample
from repository.errors import EventIdentityConflict, SchemaVersionMismatch
from repository.impl.sqlite.canonical_events import SqliteCanonicalEventRepository
from repository.impl.sqlite.connection import SqliteDatabase
from repository.impl.sqlite.databases import (
    audit_database,
    main_database,
    read_only,
)
from repository.impl.sqlite.audit import (
    SqliteAuditReadRepository,
    SqliteAuditWriteRepository,
)
from repository.impl.sqlite.raw_event_audits import SqliteRawEventAuditRepository
from repository.contract.session_data import SessionDataChanges
from repository.impl.sqlite.session_data import SqliteSessionDataRepository
from repository.impl.sqlite.shell_output import SqliteShellOutputRepository
from repository.impl.sqlite.preferences import (
    SqliteHiddenDirectoryRepository,
    SqliteNewSessionRepository,
    SqliteNotificationSettingRepository,
    SqlitePushSigningKeyRepository,
    SqlitePushSubscriptionRepository,
    SqliteTaskDismissalRepository,
    SqliteViewModeRepository,
)
from repository.impl.sqlite.raw_events import SqliteRawEventRepository
from repository.impl.sqlite.sessions import SqliteSessionRepository
from repository.impl.sqlite.schema import MAIN_MIGRATIONS, MAIN_SCHEMA_VERSION
from repository.impl.sqlite.terminal import (
    SqlitePaneWidthRepository,
)
from repository.impl.sqlite.uploads import SqliteUploadRepository
from repository.impl.sqlite.usage import SqliteAccountUsageRepository
from repository.impl.sqlite.workspace import SqliteSessionWorkspaceRepository

SESSION = SessionId("session-one")
ACTOR = ActorId("actor-one")
HARNESS = HarnessName.CODEX


@pytest.fixture
def main(tmp_path):
    return main_database(str(tmp_path / "main.db"))


def a_session(
    terminal_window_id: WindowId | None = None,
    harness_process_id: int | None = None,
) -> Session:
    return Session(
        session_id=SESSION,
        lead_actor_id=ACTOR,
        source_reference="/transcripts/one.jsonl",
        working_directory="/project",
        terminal_window_id=terminal_window_id,
        harness_process_id=harness_process_id,
    )


def a_raw_event(identity: str = "raw-one", position: str = "1") -> RawEvent:
    return RawEvent(
        raw_event_id=RawEventId(identity),
        harness=HARNESS,
        source_type="hook",
        source_name="source",
        source_position=position,
        session_id=SESSION,
        actor_id=ACTOR,
        parent_actor_id=None,
        observed_at=1000.0,
        encoding="json",
        payload=b"{}",
        source_identity="example:hook",
    )


def a_started_event(event_id: str = "event-one") -> CanonicalEvent:
    return CanonicalEvent(
        event_id=CanonicalEventId(event_id),
        session_id=SESSION,
        actor_id=ACTOR,
        turn_id=None,
        parent_actor_id=None,
        harness=HARNESS,
        occurred_at=1000.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=SessionStarted("/project", "/transcripts/one.jsonl", None, None, None, None, None),
    )


# --- the file itself ----------------------------------------------------------


def test_the_schema_is_applied_once_and_the_version_is_recorded(main):
    main.initialize()
    main.initialize()
    with main.read() as connection:
        row = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
    assert row["version"] == main.schema_version


def test_a_file_written_by_another_schema_version_refuses_to_open(tmp_path):
    first = main_database(str(tmp_path / "main.db"))
    first.initialize()
    second = SqliteDatabase(first.path, first.schema, first.schema_version + 1)
    with pytest.raises(SchemaVersionMismatch):
        second.initialize()


def test_the_main_schema_is_created_whole_with_no_migration_to_apply(tmp_path):
    """Version 4 rewrote the canonical vocabulary, so no earlier row means
    anything under it: the "migration" is deleting the file, and the DDL builds
    the whole schema on first open. `MAIN_MIGRATIONS` is empty on purpose, and a
    file from any other version is refused rather than adapted (see
    `test_a_file_written_by_another_schema_version_refuses_to_open`)."""
    assert MAIN_MIGRATIONS == {}

    database = main_database(str(tmp_path / "main.db"))
    database.initialize()

    with database.read() as connection:
        version = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert version["version"] == MAIN_SCHEMA_VERSION
    assert {"raw_events", "canonical_events", "interpretations", "shell_output"} <= tables


def test_a_read_only_database_never_creates_the_file(tmp_path):
    forensic = read_only(main_database(str(tmp_path / "absent.db")))
    forensic.initialize()
    assert not forensic.exists()


def test_a_failed_write_rolls_the_whole_transaction_back(main):
    sessions = SqliteSessionRepository(main)
    sessions.save(HARNESS, a_session())
    with pytest.raises(RuntimeError), main.write() as connection:
        connection.execute("DELETE FROM sessions")
        raise RuntimeError("boom")
    assert sessions.find(SESSION) is not None


# --- sessions -----------------------------------------------------------------


def test_a_session_upsert_writes_identity_once_and_refreshes_the_live_columns(main):
    sessions = SqliteSessionRepository(main)
    sessions.save(HARNESS, a_session())
    sessions.save(HARNESS, a_session(terminal_window_id=WindowId("7"), harness_process_id=42))
    stored = sessions.find(SESSION)
    assert stored is not None
    assert (stored.terminal_window_id, stored.harness_process_id) == ("7", 42)


def test_a_finished_session_leaves_the_watchable_set(main):
    sessions = SqliteSessionRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    raw_events = SqliteRawEventRepository(main)
    sessions.save(HARNESS, a_session())
    assert [session.session_id for session in sessions.watchable()] == [SESSION]

    raw_events.record([a_raw_event()])
    finished = CanonicalEvent(
        event_id=CanonicalEventId("event-finished"),
        session_id=SESSION,
        actor_id=ACTOR,
        turn_id=None,
        parent_actor_id=None,
        harness=HARNESS,
        occurred_at=1001.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=SessionFinished(Outcome.SUCCEEDED, None),
    )
    canonical.record_translation(
        a_raw_event(), "1", TranslationResult((finished,), "translated"), 1001.0
    )
    assert sessions.watchable() == ()


# --- raw evidence -------------------------------------------------------------


def test_re_recording_an_identical_observation_is_a_no_op(main):
    raw_events = SqliteRawEventRepository(main)
    raw_events.record([a_raw_event()])
    raw_events.record([a_raw_event()])
    assert raw_events.find(RawEventId("raw-one")) is not None


def test_reusing_an_identity_for_different_bytes_is_corruption(main):
    raw_events = SqliteRawEventRepository(main)
    raw_events.record([a_raw_event()])
    with pytest.raises(EventIdentityConflict):
        raw_events.record([a_raw_event(position="2")])


def test_resume_positions_come_back_for_every_source_in_one_call(main):
    raw_events = SqliteRawEventRepository(main)
    raw_events.record([a_raw_event("raw-one", "1")])
    second = RawEvent(
        raw_event_id=RawEventId("raw-two"),
        harness=HARNESS,
        source_type="hook",
        source_name="source",
        source_position="9",
        session_id=SESSION,
        actor_id=ACTOR,
        parent_actor_id=None,
        observed_at=1002.0,
        encoding="json",
        payload=b"{}",
        source_identity="example:other",
    )
    raw_events.record([second])
    positions = raw_events.latest_positions(["example:hook", "example:other", "example:absent"])
    assert positions == {"example:hook": "1", "example:other": "9"}


def test_the_backlog_is_evidence_without_a_verdict_in_arrival_order(main):
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    raw_events.record([a_raw_event()])
    assert [event.raw_event_id for event in raw_events.unverdicted(10)] == [RawEventId("raw-one")]
    canonical.record_translation(
        a_raw_event(), "1", TranslationResult((), "ignored_unknown"), 1000.0
    )
    assert raw_events.unverdicted(10) == ()


# --- canonical facts ----------------------------------------------------------


def test_one_translation_writes_verdict_facts_and_provenance_together(main):
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    raw_events.record([a_raw_event()])
    outcome = canonical.record_translation(
        a_raw_event(), "1", TranslationResult((a_started_event(),), "translated"), 1000.0
    )
    assert [event.event_id for event in outcome.accepted] == [CanonicalEventId("event-one")]
    stored = canonical.find(CanonicalEventId("event-one"))
    assert stored is not None
    assert stored.raw_event_ids == (RawEventId("raw-one"),)


def test_a_re_observed_fact_adds_provenance_and_is_not_re_accepted(main):
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    second = a_raw_event("raw-two", "2")
    raw_events.record([a_raw_event(), second])
    canonical.record_translation(
        a_raw_event(), "1", TranslationResult((a_started_event(),), "translated"), 1000.0
    )
    outcome = canonical.record_translation(
        second, "1", TranslationResult((a_started_event(),), "translated"), 1001.0
    )
    assert outcome.accepted == ()
    assert [event.event_id for event in outcome.deduplicated] == [CanonicalEventId("event-one")]
    stored = canonical.find(CanonicalEventId("event-one"))
    assert stored is not None
    assert set(stored.raw_event_ids) == {RawEventId("raw-one"), RawEventId("raw-two")}


def test_the_reaction_loops_page_walks_every_session_in_commit_order(main):
    """The ONE read over the fact log now, and the reason it is not per session.

    Reactions happen in the order the world saw them, not per session, so the
    loop reads across all of them from a single cursor and resumes from the last
    one it saw. The five per-session paging methods this replaced existed for the
    read-time folds, and there are none: what a session IS lives in the read
    model, written once as the facts arrived.
    """
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    other = SessionId("session-two")
    for index, session_id in enumerate((SESSION, other, SESSION)):
        raw = a_raw_event(f"raw-{index}", str(index))
        raw_events.record([raw])
        canonical.record_translation(
            raw,
            "1",
            TranslationResult((CanonicalEvent(
                event_id=CanonicalEventId(f"event-{index}"),
                session_id=session_id,
                actor_id=ACTOR,
                turn_id=None,
                parent_actor_id=None,
                harness=HARNESS,
                occurred_at=1000.0 + index,
                terminal_window_id=None,
                harness_process_id=None,
                payload=MessageCreated(
                    MessageId(f"m{index}"), MessageRole.USER, TextContent("hi"), MessagePhase.PROMPT, None
                ),
            ),), "translated"),
            1000.0 + index,
        )

    whole = canonical.page_from(0, 10)
    assert [committed.event_id for committed in whole] == [
        CanonicalEventId("event-0"),
        CanonicalEventId("event-1"),
        CanonicalEventId("event-2"),
    ]
    assert [committed.session_id for committed in whole] == [SESSION, other, SESSION]
    # Cursors ascend, and resuming from one returns exactly what follows it.
    cursors = [committed.cursor for committed in whole]
    assert cursors == sorted(cursors)
    assert [committed.event_id for committed in canonical.page_from(cursors[0], 10)] == [
        CanonicalEventId("event-1"),
        CanonicalEventId("event-2"),
    ]
    # The limit is the batch boundary, and it never skips: a smaller page is the
    # same walk in more steps.
    assert [committed.cursor for committed in canonical.page_from(0, 2)] == cursors[:2]


# --- raw-event audit ----------------------------------------------------------


def test_raw_event_audit_joins_an_observation_to_its_interpretation_and_facts(main):
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    audits = SqliteRawEventAuditRepository(main)
    raw_events.record([a_raw_event()])
    canonical.record_translation(
        a_raw_event(), "3", TranslationResult((a_started_event(),), "translated"), 1000.0
    )
    one = audits.audit(RawEventId("raw-one"))
    assert one is not None
    assert one.interpretation is not None
    assert one.interpretation.decision == "translated"
    assert one.interpretation.translator_version == "3"
    assert [item.event.event_id for item in one.interpretation.events] == [
        CanonicalEventId("event-one")
    ]
    assert audits.audits_for_session(SESSION) == (one,)


def test_uninterpreted_raw_event_audit_has_no_interpretation(main):
    raw_events = SqliteRawEventRepository(main)
    audits = SqliteRawEventAuditRepository(main)
    raw_events.record([a_raw_event()])
    one = audits.audit(RawEventId("raw-one"))
    assert one is not None and one.interpretation is None


# --- shell output -------------------------------------------------------------


def a_following(
    until: ShellFollowUntil = ShellFollowUntil.SHELL_FINISHED,
) -> ShellOutputFollowing:
    return ShellOutputFollowing(
        session_id=SESSION,
        shell_id=ShellId("op-one"),
        harness=HARNESS,
        actor_id=ACTOR,
        parent_actor_id=None,
        source_path="/tmp/output",
        chunk_source_type="chunk",
        delete_source=True,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=until,
        state=ShellFollowState.ACTIVE,
        created_at=1000.0,
    )


def test_a_following_round_trips_without_a_driver_row(main):
    outputs = SqliteShellOutputRepository(main)
    outputs.save(a_following())
    assert outputs.find_for_session(SESSION) == (a_following(),)


def test_marking_finished_ends_only_a_foreground_following(main):
    outputs = SqliteShellOutputRepository(main)
    outputs.save(a_following(until=ShellFollowUntil.SESSION_FINISHED))
    outputs.mark_shell_finished(SESSION, ShellId("op-one"))
    assert outputs.find_for_session(SESSION)[0].state == ShellFollowState.ACTIVE
    outputs.mark_finishing(SESSION, ShellId("op-one"))
    assert outputs.find_for_session(SESSION)[0].finishing


def test_expiry_returns_what_it_removed_so_the_caller_unlinks(main):
    outputs = SqliteShellOutputRepository(main)
    outputs.save(a_following())
    removed = outputs.remove_expired(2000.0)
    assert [following.source_path for following in removed] == ["/tmp/output"]
    assert outputs.find_for_session(SESSION) == ()


# --- the read model -----------------------------------------------------------


# One of each, and `replace` for the differences. A dict of defaults updated
# with kwargs is the same builder untyped: every field arrives as `object`, so a
# test could pass a string where a Literal belongs and nothing would say so.
A_SESSION = SessionFacts(
    session_id=SESSION,
    harness=HARNESS,
    state=LifecycleState.RUNNING,
    working_directory="/work",
    started_at=1.0,
    lead_actor_id=ActorId("lead"),
)
AN_ACTOR = ActorFacts(
    session_id=SESSION,
    actor_id=ActorId("lead"),
    role=ActorRole.LEAD,
    name="claude",
    state=LifecycleState.RUNNING,
)


def an_entry(entry_id: str) -> SessionEntry:
    return SessionEntry(
        entry_id=CanonicalEventId(entry_id),
        session_id=SESSION,
        actor_id=ActorId("lead"),
        parent_actor_id=None,
        turn_id=None,
        occurred_at=1.0,
        summary=None,
        body=MessageBody(MessageId(entry_id), MessageRole.USER, MessagePhase.PROMPT, TextContent("go")),
    )


def test_one_counter_stamps_the_entries_and_the_aggregate_revisions_alike(main):
    """The whole stream mechanism: an entry's cursor and an aggregate row's
    revision come from ONE monotonic counter, so "everything after C" is a single
    question with a single answer across both kinds of change."""
    store = SqliteSessionDataRepository(main)

    first = store.apply(SESSION, SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR,)), 10)
    second = store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 11)
    third = store.apply(SESSION, SessionDataChanges(actors=(replace(AN_ACTOR, status=ActorStatus.WORKING),)), 12)

    assert (first, second, third) == (1, 2, 3)
    data = store.read(SESSION)
    assert data.cursor == 3
    assert store.entries_page(SESSION, limit=10).items[0].cursor == 2
    # …and the mark moved with the rows, every time.
    assert store.progress() == 12


def test_an_aggregate_read_reports_the_high_water_mark_across_both_kinds(main):
    """A stream must not start from the aggregate's own revision: it routinely
    lags the newest entry, and starting there re-sends what the client has."""
    store = SqliteSessionDataRepository(main)
    store.apply(SESSION, SessionDataChanges(session=A_SESSION), 1)
    store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 2)

    data = store.read(SESSION)
    assert data.session.state == "running"
    assert data.cursor == 2


def test_the_counter_survives_a_restart_by_reading_what_is_already_there(main):
    """A fresh process must not hand out a cursor a client already holds."""
    first = SqliteSessionDataRepository(main)
    first.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 1)
    first.apply(SESSION, SessionDataChanges(entry=an_entry("e2")), 2)

    restarted = SqliteSessionDataRepository(main)
    assert restarted.apply(SESSION, SessionDataChanges(entry=an_entry("e3")), 3) == 3


def test_an_entry_is_written_once_however_often_its_event_is_replayed(main):
    store = SqliteSessionDataRepository(main)
    store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 1)
    store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 1)
    assert len(store.entries_page(SESSION, limit=10).items) == 1


def test_a_page_is_read_as_of_a_cursor_so_it_agrees_with_the_snapshot(main):
    store = SqliteSessionDataRepository(main)
    for ordinal in range(1, 6):
        store.apply(SESSION, SessionDataChanges(entry=an_entry("e%d" % ordinal)), ordinal)

    whole = store.entries_page(SESSION, limit=10)
    assert [entry.entry_id for entry in whole.items] == ["e1", "e2", "e3", "e4", "e5"]
    assert whole.has_more is False

    snapshot = store.entries_page(SESSION, at=3, limit=10)
    assert [entry.entry_id for entry in snapshot.items] == ["e1", "e2", "e3"]

    newest = store.entries_page(SESSION, limit=2)
    assert [entry.entry_id for entry in newest.items] == ["e4", "e5"]
    assert (newest.oldest_cursor, newest.has_more) == (4, True)
    older = store.entries_page(SESSION, before=newest.oldest_cursor, limit=2)
    assert [entry.entry_id for entry in older.items] == ["e2", "e3"]


def test_the_deltas_answer_only_what_changed_after_a_cursor(main):
    store = SqliteSessionDataRepository(main)
    store.apply(SESSION, SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR,)), 1)
    boundary = store.read(SESSION).cursor
    store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 2)
    store.apply(SESSION, SessionDataChanges(actors=(replace(AN_ACTOR, status=ActorStatus.WORKING),)), 3)

    delta = store.delta(SESSION, boundary)
    assert [entry.entry_id for entry in delta.entries] == ["e1"]
    assert delta.session is None
    assert [actor.status for actor in delta.actors] == ["working"]
    # The cursor it reached, which is what a stream sends back: without it an
    # aggregate-only change would be re-sent on every poll forever.
    assert delta.cursor == 3
    assert store.delta(SESSION, delta.cursor).empty

    across = store.changed_after(boundary)
    assert across.sessions == ()
    assert [actor.actor_id for actor in across.actors] == [ActorId("lead")]
    assert across.cursor == 3
    assert store.changed_after(0).sessions[0].session_id == SESSION


def test_an_entry_body_decodes_as_the_shape_its_own_type_names(main):
    """The payload column is a closed typed document, not a blob: what comes back
    is the body class the `entry_type` names, validated."""
    store = SqliteSessionDataRepository(main)
    store.apply(
        SESSION,
        SessionDataChanges(
            entry=replace(
                an_entry("e1"),
                body=ShellStartedBody(ShellId("sh1"), TextContent("make test"), ExecutionMode.BACKGROUND),
            )
        ),
        1,
    )
    stored = store.entries_page(SESSION, limit=10).items[0]
    assert stored.entry_type == "shell_started"
    assert stored.body == ShellStartedBody(ShellId("sh1"), TextContent("make test"), ExecutionMode.BACKGROUND)


def test_clearing_the_read_model_resets_the_cursor_space_it_handed_out(main):
    """A rebuild starts the feed again from one. Leaving the AUTOINCREMENT mark
    behind would start it above every cursor a client already holds, and every
    poll would come back empty."""
    store = SqliteSessionDataRepository(main)
    store.apply(SESSION, SessionDataChanges(session=A_SESSION, entry=an_entry("e1")), 7)
    store.clear()

    assert store.read(SESSION) is None
    assert store.visible() == ()
    assert store.progress() == 0
    assert store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 1) == 1
    assert store.entries_page(SESSION, limit=10).items[0].cursor == 1


def test_the_list_view_reads_every_session_with_its_own_cursor(main):
    store = SqliteSessionDataRepository(main)
    other = SessionId("session-two")
    store.apply(SESSION, SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR,)), 1)
    store.apply(
        other,
        SessionDataChanges(
            session=replace(A_SESSION, session_id=other, title="Other"),
            actors=(replace(AN_ACTOR, session_id=other),),
        ),
        2,
    )
    store.apply(SESSION, SessionDataChanges(entry=an_entry("e1")), 3)

    listed = {data.session.session_id: data for data in store.visible()}
    assert set(listed) == {SESSION, other}
    assert listed[SESSION].cursor == 3
    assert listed[other].session.title == "Other"
    assert [actor.actor_id for actor in listed[other].actors] == [ActorId("lead")]


# --- the session workspace ----------------------------------------------------


def test_an_older_composer_draft_never_clobbers_a_newer_one(main):
    workspace = SqliteSessionWorkspaceRepository(main)
    assert workspace.save_composer_draft(SESSION, ComposerDraft("second", "web", 2.0))
    assert not workspace.save_composer_draft(SESSION, ComposerDraft("first", "web", 1.0))
    stored = workspace.find(SESSION)
    assert stored is not None and stored.draft is not None
    assert stored.draft.text == "second"


def test_the_queue_and_the_dialog_round_trip_as_rows(main):
    workspace = SqliteSessionWorkspaceRepository(main)
    workspace.save_composer_queue(
        SESSION, ComposerQueue((QueuedMessage("one"), QueuedMessage("two")), "web")
    )
    workspace.save_dialog_draft(
        SESSION,
        DialogDraft(
            AttentionId("attention-one"),
            (AnswerSelection(("a", "b"), "other text"),),
            "web",
        ),
    )
    stored = workspace.find(SESSION)
    assert stored is not None
    assert stored.queue is not None
    assert [message.text for message in stored.queue.items] == ["one", "two"]
    assert stored.dialog is not None
    assert stored.dialog.answers[0].selected == ("a", "b")
    assert stored.dialog.answers[0].other == "other text"


def test_saving_a_queue_replaces_the_previous_one(main):
    workspace = SqliteSessionWorkspaceRepository(main)
    workspace.save_composer_queue(SESSION, ComposerQueue((QueuedMessage("one"),), "web"))
    workspace.save_composer_queue(SESSION, ComposerQueue((), "web"))
    stored = workspace.find(SESSION)
    assert stored is not None and stored.queue is None


# --- preferences --------------------------------------------------------------


def test_a_view_mode_is_stored_and_cleared(main):
    view_modes = SqliteViewModeRepository(main)
    assert view_modes.view_mode(SESSION) is None
    view_modes.set_view_mode(SESSION, "focus")
    assert view_modes.view_mode(SESSION) == "focus"
    view_modes.clear_view_mode(SESSION)
    assert view_modes.view_mode(SESSION) is None


def test_alerting_defaults_on_and_mutes_come_back_in_one_call(main):
    notifications = SqliteNotificationSettingRepository(main)
    assert notifications.alerting_enabled()
    notifications.set_alerting_enabled(False)
    assert not notifications.alerting_enabled()
    notifications.set_muted(SESSION, True)
    assert notifications.muted_session_ids() == frozenset({SESSION})
    notifications.set_muted(SESSION, False)
    assert notifications.muted_session_ids() == frozenset()


def test_hiding_a_directory_twice_keeps_the_newer_stamp(main):
    directories = SqliteHiddenDirectoryRepository(main)
    directories.hide("/project", 1.0)
    directories.hide("/project", 2.0)
    assert [(entry.working_directory, entry.hidden_at) for entry in directories.hidden()] == [
        ("/project", 2.0)
    ]


def test_a_stale_new_session_draft_is_rejected_and_the_map_is_pruned(main):
    new_sessions = SqliteNewSessionRepository(main)
    assert not new_sessions.save_draft(NewSessionDraft("/a", "newer", 2.0), 10).stale
    assert new_sessions.save_draft(NewSessionDraft("/a", "older", 1.0), 10).stale
    assert [draft.text for draft in new_sessions.drafts()] == ["newer"]

    for index in range(5):
        new_sessions.save_draft(NewSessionDraft(f"/dir{index}", "x", float(index + 10)), 2)
    assert len(new_sessions.drafts()) == 2


def test_new_session_preferences_round_trip(main):
    new_sessions = SqliteNewSessionRepository(main)
    assert new_sessions.preferences() is None
    new_sessions.save_preferences(NewSessionPreferences("/project", HARNESS, "opus", "high"))
    assert new_sessions.preferences() == NewSessionPreferences("/project", HARNESS, "opus", "high")


def test_task_dismissals_store_the_id_set_and_prune_by_session(main):
    dismissals = SqliteTaskDismissalRepository(main)
    dismissals.dismiss(SESSION, [TaskId("t1"), TaskId("t2")], 1.0, 10)
    assert dismissals.dismissed_task_ids(SESSION) == frozenset({TaskId("t1"), TaskId("t2")})
    dismissals.restore(SESSION)
    assert dismissals.dismissed_task_ids(SESSION) == frozenset()

    for index in range(4):
        dismissals.dismiss(SessionId(f"s{index}"), [TaskId("t")], float(index), 2)
    remaining = [
        session
        for session in (SessionId(f"s{index}") for index in range(4))
        if dismissals.dismissed_task_ids(session)
    ]
    assert len(remaining) == 2


def test_push_subscriptions_upsert_by_endpoint(main):
    subscriptions = SqlitePushSubscriptionRepository(main)
    subscriptions.upsert(PushSubscription("https://push/1", "p", "a", "device", None, 1.0))
    subscriptions.upsert(PushSubscription("https://push/1", "p2", "a2", "device", "phone", 2.0))
    assert len(subscriptions.subscriptions()) == 1
    assert subscriptions.subscriptions()[0].device_label == "phone"
    subscriptions.remove("https://push/1")
    assert subscriptions.subscriptions() == ()


def test_the_push_signing_keypair_is_a_singleton(main):
    keys = SqlitePushSigningKeyRepository(main)
    assert keys.keypair() is None
    keys.save_keypair(PushSigningKeypair("pem", "pub"))
    keys.save_keypair(PushSigningKeypair("pem2", "pub2"))
    assert keys.keypair() == PushSigningKeypair("pem2", "pub2")


# --- terminal -----------------------------------------------------------------


def test_a_pane_width_is_absent_until_remembered(main):
    widths = SqlitePaneWidthRepository(main)
    assert widths.width_percent("/project") is None
    widths.remember_width("/project", 40)
    assert widths.width_percent("/project") == 40


# test_toggling_a_view_opens_it_and_toggling_again_closes_it lived here. Which
# file views the mirror has expanded is the PANE's own state now: it holds every
# byte it draws, so expanding one needs nothing from the daemon and the daemon
# has no business remembering it.


# --- usage --------------------------------------------------------------------


def test_a_usage_snapshot_replaces_its_windows(main):
    usage = SqliteAccountUsageRepository(main)
    usage.record(
        AccountUsageSnapshot(
            HARNESS,
            "account-one",
            "One",
            10.0,
            (UsageWindowSample("five_hour", Decimal("12.5"), 99.0),),
        )
    )
    usage.record(
        AccountUsageSnapshot(
            HARNESS,
            "account-one",
            "One",
            20.0,
            (UsageWindowSample("seven_day", Decimal("3"), None),),
        )
    )
    snapshots = usage.snapshots()
    assert len(snapshots) == 1
    assert [window.key for window in snapshots[0].windows] == ["seven_day"]
    assert snapshots[0].captured_at == 20.0


def test_an_account_less_snapshot_round_trips_as_none(main):
    usage = SqliteAccountUsageRepository(main)
    usage.record(AccountUsageSnapshot(HARNESS, None, "default", 1.0, ()))
    assert usage.snapshots()[0].account_id is None


# --- uploads ------------------------------------------------------------------


def test_expired_uploads_come_back_so_the_caller_can_unlink(main):
    uploads = SqliteUploadRepository(main)
    uploads.record(StoredUpload("u1", SESSION, "a.png", "image/png", 3, "/tmp/a.png", 1.0))
    uploads.record(StoredUpload("u2", None, "b.png", "image/png", 3, "/tmp/b.png", 100.0))
    removed = uploads.remove_expired(50.0)
    assert [upload.stored_path for upload in removed] == ["/tmp/a.png"]
    assert uploads.remove_expired(50.0) == ()


# --- audit --------------------------------------------------------------


def test_errors_are_written_and_counted_per_session(tmp_path):
    database = audit_database(str(tmp_path / "audit.db"))
    writes = SqliteAuditWriteRepository(database)
    reads = SqliteAuditReadRepository(read_only(database))
    writes.record_error(
        ApplicationErrorRecord(str(SESSION), "script", "where", "trace", "context", 1, 5.0)
    )
    stored = reads.errors_for_session(SESSION)
    assert [error.action for error in stored] == ["where"]
    assert reads.error_counts() == {SESSION: 1}


def test_the_audit_writer_never_raises_when_its_file_is_unusable(tmp_path):
    unusable = audit_database(str(tmp_path / "missing" / "nested" / "audit.db"))
    unusable.path = str(tmp_path)  # a directory: every open will fail
    writes = SqliteAuditWriteRepository(unusable)
    writes.record_error(ApplicationErrorRecord("", "s", "f", "t", "c", 1, 1.0))
    writes.record_state_file(StateFileRecord("", "p", "a", "c", "s", 1, 1.0))
    writes.record_spawn(SpawnRecord("", "s", 2, "[]", "why", 1.0))
    assert writes.open_stream(StreamOpened("", "kind", "", "", "", 1, 1.0)) is None
    writes.close_stream(None, "done", 0)


def test_a_stream_row_is_opened_and_closed_through_its_handle(tmp_path):
    database = audit_database(str(tmp_path / "audit.db"))
    writes = SqliteAuditWriteRepository(database)
    handle = writes.open_stream(StreamOpened(str(SESSION), "mirror", "", "", "", 1, 1.0))
    assert handle is not None
    writes.close_stream(handle, "finished", 12)
    with database.read() as connection:
        row = connection.execute("SELECT * FROM streams WHERE id=?", (handle.stream_id,)).fetchone()
    assert (row["end_reason"], row["lines_emitted"]) == ("finished", 12)


def test_the_audit_is_a_no_op_when_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv("BAQYLAU_AUDIT", "0")
    database = audit_database(str(tmp_path / "audit.db"))
    writes = SqliteAuditWriteRepository(database)
    writes.record_error(ApplicationErrorRecord(str(SESSION), "s", "f", "t", "c", 1, 1.0))
    reads = SqliteAuditReadRepository(read_only(database))
    assert reads.errors_for_session(SESSION) == ()


# --- the clock is not the repository's ----------------------------------------


def test_nothing_in_the_layer_needs_a_real_clock_to_be_exercised(main):
    """Every timestamp above is supplied by the caller, which is why these
    tests assert on exact values rather than on ranges."""
    uploads = SqliteUploadRepository(main)
    uploads.record(StoredUpload("u", None, "n", "text/plain", 1, "/tmp/n", 0.0))
    assert uploads.remove_expired(time.time())[0].created_at == 0.0
