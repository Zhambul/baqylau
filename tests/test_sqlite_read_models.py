# Copyright (c) 2026 Zhambyl Yermagambet
"""Provide test sqlite read models."""

from __future__ import annotations

from tests import (
    sqlite_domain_dependencies as domain_dependencies,
    sqlite_library_dependencies as library_dependencies,
    sqlite_repository_dependencies as repository_dependencies,
    sqlite_test_dependencies as test_dependencies,
    sqlite_test_migrations,
    sqlite_value_dependencies as standard_dependencies,
)
from tests.harness_names import CODEX_HARNESS

SESSION = domain_dependencies.domain_ids.SessionId("session-one")
HARNESS = CODEX_HARNESS
FINISHED_SESSION_TIME = 2.0
NEWER_PREFERENCE_TIME = 2.0
FIRST_REQUEST_ID = "request-one"
FIRST_MESSAGE_TEXT = "one"
LEAD_ACTOR_ID_TEXT = "lead"
FIRST_ENTRY_ID = "e1"
SEND_ORIGIN = "send"
REPLAYED_ENTRY_CURSOR = 7
LATEST_ENTRY_CURSOR = 3
A_SESSION = domain_dependencies.session_state.SessionFacts(
    session_id=SESSION,
    harness=HARNESS,
    state=domain_dependencies.lifecycle.LifecycleState.RUNNING,
    working_directory="/work",
    started_at=1.0,
    lead_actor_id=domain_dependencies.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
)
AN_ACTOR = standard_dependencies.actor_state.ActorFacts(
    session_id=SESSION,
    actor_id=domain_dependencies.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT),
    role=domain_dependencies.messaging.ActorRole.LEAD,
    name="claude",
    state=domain_dependencies.lifecycle.LifecycleState.RUNNING,
)


def test_deltas_answer_only_what_changed(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify the deltas answer only what changed after a cursor."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    store.apply(SESSION, repository_dependencies.SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR,)), 1)
    stored = store.read(SESSION)
    assert stored is not None
    boundary = stored.cursor
    store.apply(
        SESSION,
        repository_dependencies.SessionDataChanges(entry=sqlite_test_migrations.an_entry(FIRST_ENTRY_ID)),
        2,
    )
    store.apply(
        SESSION, repository_dependencies.SessionDataChanges(actors=(sqlite_test_migrations.working_actor(),)), 3,
    )
    delta = store.delta(SESSION, boundary)
    assert (
        [entry.entry_id for entry in delta.entries],
        delta.session,
        [actor.status for actor in delta.actors],
        delta.cursor,
    ) == ([FIRST_ENTRY_ID], None, ["working"], 3)
    assert store.delta(SESSION, delta.cursor).empty
    across = store.changed_after(boundary)
    assert (
        across.sessions,
        [actor.actor_id for actor in across.actors],
        across.cursor,
        store.changed_after(0).sessions[0].session_id,
    ) == ((), [domain_dependencies.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT)], 3, SESSION)


def test_entry_body_decodes_as_shape_its_own_type(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify an entry body decodes as the shape its own type names.

    The payload column is a closed typed document, not a blob: what comes back
        is the body class the `entry_type` names, validated.
    """
    store = test_dependencies.SqliteSessionDataRepository(main)
    store.apply(
        SESSION,
        repository_dependencies.SessionDataChanges(
            entry=standard_dependencies.replace(
                sqlite_test_migrations.an_entry(FIRST_ENTRY_ID),
                body=library_dependencies.entry_shells.ShellStartedBody(
                    domain_dependencies.domain_ids.ShellId("sh1"),
                    library_dependencies.domain_content.TextContent("make test"),
                    domain_dependencies.outcomes.ExecutionMode.BACKGROUND,
                ),
            ),
        ),
        1,
    )
    stored = store.entries_page(SESSION, limit=10).entries[0]
    assert stored.entry_type == "shell_started"
    assert stored.body == library_dependencies.entry_shells.ShellStartedBody(
        domain_dependencies.domain_ids.ShellId("sh1"),
        library_dependencies.domain_content.TextContent("make test"),
        domain_dependencies.outcomes.ExecutionMode.BACKGROUND,
    )


def test_clearing_read_model_keeps_replayed_canon(main: repository_dependencies.SqliteDatabase) -> None:
    """A rebuild gives a fact the same cursor it had before the clear."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    store.apply(
        SESSION,
        repository_dependencies.SessionDataChanges(
            session=A_SESSION, entry=sqlite_test_migrations.an_entry(FIRST_ENTRY_ID),
        ),
        REPLAYED_ENTRY_CURSOR,
    )
    store.clear()
    assert store.read(SESSION) is None
    assert not store.visible()
    assert store.progress() == 0
    assert (
        store.apply(
            SESSION,
            repository_dependencies.SessionDataChanges(entry=sqlite_test_migrations.an_entry(FIRST_ENTRY_ID)),
            REPLAYED_ENTRY_CURSOR,
        )
        == REPLAYED_ENTRY_CURSOR
    )
    assert store.entries_page(SESSION, limit=10).entries[0].cursor == REPLAYED_ENTRY_CURSOR


def test_list_view_reads_every_session_with_its(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify the list view reads every session with its own cursor."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    other = domain_dependencies.domain_ids.SessionId("session-two")
    store.apply(SESSION, repository_dependencies.SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR,)), 1)
    store.apply(
        other,
        repository_dependencies.SessionDataChanges(
            session=standard_dependencies.replace(A_SESSION, session_id=other, title="Other"),
            actors=(standard_dependencies.replace(AN_ACTOR, session_id=other),),
        ),
        2,
    )
    store.apply(
        SESSION,
        repository_dependencies.SessionDataChanges(entry=sqlite_test_migrations.an_entry(FIRST_ENTRY_ID)),
        LATEST_ENTRY_CURSOR,
    )
    listed = {session_record.session.session_id: session_record for session_record in store.visible()}
    assert set(listed) == {SESSION, other}
    assert listed[SESSION].cursor == LATEST_ENTRY_CURSOR
    assert listed[other].session.title == "Other"
    other_actors = listed[other].actors
    assert [actor.actor_id for actor in other_actors] == [domain_dependencies.domain_ids.ActorId(LEAD_ACTOR_ID_TEXT)]


def test_running_list_does_not_read_finished(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify the running list does not read finished session aggregates."""
    store = test_dependencies.SqliteSessionDataRepository(main)
    finished_id = domain_dependencies.domain_ids.SessionId("session-finished")
    store.apply(SESSION, repository_dependencies.SessionDataChanges(session=A_SESSION, actors=(AN_ACTOR,)), 1)
    store.apply(
        finished_id,
        repository_dependencies.SessionDataChanges(
            session=standard_dependencies.replace(
                A_SESSION,
                session_id=finished_id,
                state=domain_dependencies.lifecycle.LifecycleState.FINISHED,
                finished_at=FINISHED_SESSION_TIME,
            ),
            actors=(
                standard_dependencies.replace(
                    AN_ACTOR, session_id=finished_id, state=domain_dependencies.lifecycle.LifecycleState.FINISHED,
                ),
            ),
        ),
        2,
    )
    store.apply(
        SESSION,
        repository_dependencies.SessionDataChanges(entry=sqlite_test_migrations.an_entry(FIRST_ENTRY_ID)),
        LATEST_ENTRY_CURSOR,
    )
    running = store.running()
    assert [session_record.session.session_id for session_record in running] == [SESSION]
    assert running[0].actors == (AN_ACTOR,)
    assert running[0].cursor == LATEST_ENTRY_CURSOR


def test_older_composer_draft_never_clobbers(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify an older composer draft never clobbers a newer one."""
    workspace = test_dependencies.SqliteSessionWorkspaceRepository(main)
    assert workspace.save_composer_draft(
        SESSION, standard_dependencies.composer.ComposerDraft("second", "web", NEWER_PREFERENCE_TIME),
    )
    assert not workspace.save_composer_draft(SESSION, standard_dependencies.composer.ComposerDraft("first", "web", 1.0))
    stored = workspace.find(SESSION)
    assert stored is not None
    assert stored.draft is not None
    assert stored.draft.text == "second"


def test_queue_and_dialog_round_trip_as_rows(main: repository_dependencies.SqliteDatabase) -> None:
    """Verify the queue and the dialog round trip as rows."""
    workspace = test_dependencies.SqliteSessionWorkspaceRepository(main)
    workspace.enqueue_composer_message(
        SESSION,
        standard_dependencies.composer.QueuedMessage(
            domain_dependencies.domain_ids.RequestId(FIRST_REQUEST_ID), FIRST_MESSAGE_TEXT,
        ),
        SEND_ORIGIN,
    )
    workspace.enqueue_composer_message(
        SESSION,
        standard_dependencies.composer.QueuedMessage(domain_dependencies.domain_ids.RequestId("request-two"), "two"),
        SEND_ORIGIN,
    )
    workspace.save_dialog_draft(
        SESSION,
        library_dependencies.dialogs.DialogDraft(
            domain_dependencies.domain_ids.AttentionId("attention-one"),
            (library_dependencies.dialogs.AnswerSelection(("a", "b"), "other text"),),
            "web",
        ),
    )
    stored = workspace.find(SESSION)
    assert stored is not None
    assert stored.queue is not None
    assert stored.dialog is not None
    assert (
        [message.text for message in stored.queue.messages],
        [message.request_id for message in stored.queue.messages],
        stored.dialog.answers[0].selected,
        stored.dialog.answers[0].other,
    ) == ([FIRST_MESSAGE_TEXT, "two"], [FIRST_REQUEST_ID, "request-two"], ("a", "b"), "other text")
