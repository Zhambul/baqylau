"""The storage layer on its own: every repository against a real database.

These tests build repositories directly, with no application graph and no
daemon, which is the whole point of the contract — a store that can only be
exercised through the thing that composes it is a store nobody tests.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Literal

import pytest

from audit.models import (
    ApplicationErrorRecord,
    SpawnRecord,
    StateFileRecord,
    StreamOpened,
)
from domain.events import CanonicalEvent, MessageCreated, SessionFinished, SessionStarted
from domain.ids import (
    ActorId,
    AttentionId,
    CanonicalEventId,
    MessageId,
    OperationId,
    RawEventId,
    SessionId,
    TaskId,
)
from domain.operations import OperationOutputFollowing
from domain.preferences import (
    NewSessionDraft,
    NewSessionPreferences,
    PushSigningKeypair,
    PushSubscription,
)
from domain.uploads import StoredUpload
from domain.values import TextContent
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
from repository.impl.sqlite.operation_output import SqliteOperationOutputRepository
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
from repository.impl.sqlite.schema import MAIN_SCHEMA
from repository.impl.sqlite.terminal import (
    SqliteContentViewRepository,
    SqlitePaneWidthRepository,
)
from repository.impl.sqlite.uploads import SqliteUploadRepository
from repository.impl.sqlite.usage import SqliteAccountUsageRepository
from repository.impl.sqlite.workspace import SqliteSessionWorkspaceRepository

SESSION = SessionId("session-one")
ACTOR = ActorId("actor-one")


@pytest.fixture
def main(tmp_path):
    return main_database(str(tmp_path / "main.db"))


def a_session(
    terminal_window_id: str | None = None,
    harness_process_id: int | None = None,
) -> Session:
    return Session(
        session_id=SESSION,
        lead_actor_id=ACTOR,
        harness_session_id="harness-one",
        source_reference="/transcripts/one.jsonl",
        working_directory="/project",
        terminal_window_id=terminal_window_id,
        harness_process_id=harness_process_id,
    )


def a_raw_event(identity: str = "raw-one", position: str = "1") -> RawEvent:
    return RawEvent(
        raw_event_id=RawEventId(identity),
        harness="example",
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
        harness="example",
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


def test_main_schema_v1_migrates_interpretation_audit_tables_without_losing_rows(tmp_path):
    path = str(tmp_path / "main.db")
    legacy_schema = MAIN_SCHEMA.replace("interpretations", "translation_records").replace(
        "interpretation_events", "canonical_provenance"
    )
    legacy = SqliteDatabase(path, legacy_schema, 1)
    legacy.initialize()
    with legacy.write() as connection:
        connection.execute(
            "INSERT INTO raw_events(raw_event_id, session_id, harness, source_type, "
            "source_identity, source_name, source_position, actor_id, observed_at, "
            "encoding, payload) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("raw-one", "session-one", "example", "hook", "source", "hook", "1", "actor", 1.0, "json", b"{}"),
        )
        connection.execute(
            "INSERT INTO translation_records(raw_event_id, translator_version, decision, "
            "reason, completed_at) VALUES(?,?,?,?,?)",
            ("raw-one", "1", "translated", None, 2.0),
        )
        connection.execute(
            "INSERT INTO canonical_events(event_id, schema_version, event_type, session_id, "
            "actor_id, harness, accepted_at, payload) VALUES(?,?,?,?,?,?,?,?)",
            ("event-one", 1, "message.created", "session-one", "actor", "example", 2.0, "{}"),
        )
        connection.execute(
            "INSERT INTO canonical_provenance(event_id, raw_event_id, event_order, "
            "storage_result) VALUES(?,?,?,?)",
            ("event-one", "raw-one", 0, "accepted"),
        )

    migrated = main_database(path)
    migrated.initialize()
    with migrated.read() as connection:
        version = connection.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        interpretation = connection.execute("SELECT * FROM interpretations").fetchone()
        event = connection.execute("SELECT * FROM interpretation_events").fetchone()
        tables = {
            row["name"]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert version["version"] == 2
    assert interpretation["raw_event_id"] == "raw-one"
    assert interpretation["decision"] == "translated"
    assert (event["event_id"], event["raw_event_id"], event["storage_result"]) == (
        "event-one",
        "raw-one",
        "accepted",
    )
    assert "translation_records" not in tables
    assert "canonical_provenance" not in tables


def test_a_read_only_database_never_creates_the_file(tmp_path):
    forensic = read_only(main_database(str(tmp_path / "absent.db")))
    forensic.initialize()
    assert not forensic.exists()


def test_a_failed_write_rolls_the_whole_transaction_back(main):
    sessions = SqliteSessionRepository(main)
    sessions.save("example", a_session())
    with pytest.raises(RuntimeError), main.write() as connection:
        connection.execute("DELETE FROM sessions")
        raise RuntimeError("boom")
    assert sessions.find(SESSION) is not None


# --- sessions -----------------------------------------------------------------


def test_a_session_upsert_writes_identity_once_and_refreshes_the_live_columns(main):
    sessions = SqliteSessionRepository(main)
    sessions.save("example", a_session())
    sessions.save("example", a_session(terminal_window_id="7", harness_process_id=42))
    stored = sessions.find(SESSION)
    assert stored is not None
    assert (stored.terminal_window_id, stored.harness_process_id) == ("7", 42)
    assert stored.harness_session_id == "harness-one"


def test_a_finished_session_leaves_the_watchable_set(main):
    sessions = SqliteSessionRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    raw_events = SqliteRawEventRepository(main)
    sessions.save("example", a_session())
    assert [session.session_id for session in sessions.watchable()] == [SESSION]

    raw_events.record([a_raw_event()])
    finished = CanonicalEvent(
        event_id=CanonicalEventId("event-finished"),
        session_id=SESSION,
        actor_id=ACTOR,
        turn_id=None,
        parent_actor_id=None,
        harness="example",
        occurred_at=1001.0,
        terminal_window_id=None,
        harness_process_id=None,
        payload=SessionFinished("succeeded", None),
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
        harness="example",
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


def test_the_newest_cursor_per_session_comes_back_in_one_call(main):
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    raw_events.record([a_raw_event()])
    canonical.record_translation(
        a_raw_event(), "1", TranslationResult((a_started_event(),), "translated"), 1000.0
    )
    cursors = canonical.latest_session_cursors([SESSION, SessionId("missing")], None)
    assert set(cursors) == {SESSION}


def test_paging_walks_a_session_forwards_and_backwards(main):
    raw_events = SqliteRawEventRepository(main)
    canonical = SqliteCanonicalEventRepository(main)
    for index in range(3):
        raw = a_raw_event(f"raw-{index}", str(index))
        raw_events.record([raw])
        message = CanonicalEvent(
            event_id=CanonicalEventId(f"event-{index}"),
            session_id=SESSION,
            actor_id=ACTOR,
            turn_id=None,
            parent_actor_id=None,
            harness="example",
            occurred_at=1000.0 + index,
            terminal_window_id=None,
            harness_process_id=None,
            payload=MessageCreated(MessageId(f"m{index}"), "user", TextContent("hi"), "prompt", None),
        )
        canonical.record_translation(
            raw, "1", TranslationResult((message,), "translated"), 1000.0 + index
        )
    latest = canonical.latest_cursor()
    assert latest is not None
    through = canonical.page_through(SESSION, latest)
    assert len(through.events) == 3
    tail = canonical.page_tail(SESSION, latest, 2)
    assert [event.event.event_id for event in tail.events] == [
        CanonicalEventId("event-1"),
        CanonicalEventId("event-2"),
    ]
    assert tail.has_more
    after = canonical.page_after(SESSION, through.events[0].cursor, 10)
    assert len(after.events) == 2
    between = canonical.events_between(
        SESSION, through.events[0].cursor, through.events[1].cursor
    )
    assert [event.event.event_id for event in between] == [CanonicalEventId("event-1")]
    of_type = canonical.events_of_types(SESSION, ("message.created",), latest)
    assert len(of_type) == 3


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


# --- operation output ---------------------------------------------------------


def a_following(
    until: Literal["operation_finished", "session_finished"] = "operation_finished",
) -> OperationOutputFollowing:
    return OperationOutputFollowing(
        session_id=SESSION,
        operation_id=OperationId("op-one"),
        harness="example",
        actor_id=ACTOR,
        parent_actor_id=None,
        source_path="/tmp/output",
        chunk_source_type="chunk",
        delete_source=True,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=until,
        state="active",
        created_at=1000.0,
    )


def test_a_following_round_trips_without_a_driver_row(main):
    outputs = SqliteOperationOutputRepository(main)
    outputs.save(a_following())
    assert outputs.find_for_session(SESSION) == (a_following(),)


def test_marking_finished_ends_only_a_foreground_following(main):
    outputs = SqliteOperationOutputRepository(main)
    outputs.save(a_following(until="session_finished"))
    outputs.mark_operation_finished(SESSION, OperationId("op-one"))
    assert outputs.find_for_session(SESSION)[0].state == "active"
    outputs.mark_finishing(SESSION, OperationId("op-one"))
    assert outputs.find_for_session(SESSION)[0].finishing


def test_expiry_returns_what_it_removed_so_the_caller_unlinks(main):
    outputs = SqliteOperationOutputRepository(main)
    outputs.save(a_following())
    removed = outputs.remove_expired(2000.0)
    assert [following.source_path for following in removed] == ["/tmp/output"]
    assert outputs.find_for_session(SESSION) == ()


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
    new_sessions.save_preferences(NewSessionPreferences("/project", "example", "opus", "high"))
    assert new_sessions.preferences() == NewSessionPreferences("/project", "example", "opus", "high")


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


def test_toggling_a_view_opens_it_and_toggling_again_closes_it(main):
    views = SqliteContentViewRepository(main)
    assert views.toggle("event:field", 1.0) is True
    assert views.opened() == frozenset({"event:field"})
    assert views.toggle("event:field", 2.0) is False
    assert views.opened() == frozenset()


# --- usage --------------------------------------------------------------------


def test_a_usage_snapshot_replaces_its_windows(main):
    usage = SqliteAccountUsageRepository(main)
    usage.record(
        AccountUsageSnapshot(
            "example",
            "account-one",
            "One",
            10.0,
            (UsageWindowSample("five_hour", Decimal("12.5"), 99.0),),
        )
    )
    usage.record(
        AccountUsageSnapshot(
            "example",
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
    usage.record(AccountUsageSnapshot("example", None, "default", 1.0, ()))
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
