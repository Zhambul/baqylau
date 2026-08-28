"""`sqlite3.Row` to row DTO. The only module in the tree that names the driver's row type.

Keeping this separate from `mapper/` is what lets the mappers be driver-free and
testable without a database: they take a row DTO, which is a plain dataclass.
"""

from __future__ import annotations

import sqlite3

from repository.model.audit import ErrorRow, SpawnRow, StateFileRow, StreamRow
from repository.model.facts import (
    CanonicalEventRow,
    SessionDataActorRow,
    SessionDataRow,
    SessionEntryRow,
    ShellOutputRow,
    RawEventRow,
    SessionRow,
)
from repository.model.preferences import (
    HiddenDirectoryRow,
    NewSessionDraftRow,
    NewSessionPreferenceRow,
    PushSigningKeyRow,
    PushSubscriptionRow,
    SessionViewModeRow,
)
from repository.model.uploads import UploadRow
from repository.model.workspace import (
    ComposerQueueItemRow,
    DialogAnswerRow,
    DialogAnswerSelectionRow,
    SessionWorkspaceRow,
)


def session(row: sqlite3.Row) -> SessionRow:
    return SessionRow(
        session_id=row["session_id"],
        lead_actor_id=row["lead_actor_id"],
        harness=row["harness"],
        source_reference=row["source_reference"],
        working_directory=row["working_directory"],
        project_directory=row["project_directory"],
        terminal_window_id=row["terminal_window_id"],
        harness_process_id=row["harness_process_id"],
        created_at=row["created_at"],
    )


def raw_event(row: sqlite3.Row) -> RawEventRow:
    return RawEventRow(
        id=row["id"],
        raw_event_id=row["raw_event_id"],
        session_id=row["session_id"],
        harness=row["harness"],
        source_type=row["source_type"],
        source_identity=row["source_identity"],
        source_name=row["source_name"],
        source_position=row["source_position"],
        actor_id=row["actor_id"],
        parent_actor_id=row["parent_actor_id"],
        observed_at=row["observed_at"],
        encoding=row["encoding"],
        payload=row["payload"],
        payload_codec=row["payload_codec"],
        terminal_window_id=row["terminal_window_id"],
        harness_process_id=row["harness_process_id"],
        account_id=row["account_id"],
        account_display_name=row["account_display_name"],
    )


def canonical_event(row: sqlite3.Row) -> CanonicalEventRow:
    return CanonicalEventRow(
        cursor=row["cursor"],
        event_id=row["event_id"],
        schema_version=row["schema_version"],
        event_type=row["event_type"],
        session_id=row["session_id"],
        actor_id=row["actor_id"],
        turn_id=row["turn_id"],
        parent_actor_id=row["parent_actor_id"],
        harness=row["harness"],
        occurred_at=row["occurred_at"],
        terminal_window_id=row["terminal_window_id"],
        harness_process_id=row["harness_process_id"],
        accepted_at=row["accepted_at"],
        payload=row["payload"],
    )


def shell_output(row: sqlite3.Row) -> ShellOutputRow:
    return ShellOutputRow(
        session_id=row["session_id"],
        shell_id=row["shell_id"],
        harness=row["harness"],
        actor_id=row["actor_id"],
        parent_actor_id=row["parent_actor_id"],
        source_path=row["source_path"],
        chunk_source_type=row["chunk_source_type"],
        delete_source=row["delete_source"],
        initial_size=row["initial_size"],
        initial_modified_at=row["initial_modified_at"],
        wait_for_source_change=row["wait_for_source_change"],
        until=row["until"],
        state=row["state"],
        created_at=row["created_at"],
    )


def session_workspace(row: sqlite3.Row) -> SessionWorkspaceRow:
    return SessionWorkspaceRow(
        session_id=row["session_id"],
        composer_text=row["composer_text"],
        composer_origin=row["composer_origin"],
        composer_sequence=row["composer_sequence"],
        queue_origin=row["queue_origin"],
        dialog_attention_id=row["dialog_attention_id"],
        dialog_origin=row["dialog_origin"],
    )


def composer_queue_item(row: sqlite3.Row) -> ComposerQueueItemRow:
    return ComposerQueueItemRow(
        session_id=row["session_id"],
        position=row["position"],
        request_id=row["request_id"],
        text=row["text"],
    )


def dialog_answer(row: sqlite3.Row) -> DialogAnswerRow:
    return DialogAnswerRow(
        session_id=row["session_id"],
        prompt_index=row["prompt_index"],
        other_text=row["other_text"],
    )


def dialog_answer_selection(row: sqlite3.Row) -> DialogAnswerSelectionRow:
    return DialogAnswerSelectionRow(
        session_id=row["session_id"],
        prompt_index=row["prompt_index"],
        selection_index=row["selection_index"],
        selected_value=row["selected_value"],
    )


def session_view_mode(row: sqlite3.Row) -> SessionViewModeRow:
    return SessionViewModeRow(session_id=row["session_id"], view_mode=row["view_mode"])


def hidden_directory(row: sqlite3.Row) -> HiddenDirectoryRow:
    return HiddenDirectoryRow(
        working_directory=row["working_directory"],
        hidden_at=row["hidden_at"],
    )


def new_session_preference(row: sqlite3.Row) -> NewSessionPreferenceRow:
    return NewSessionPreferenceRow(
        id=row["id"],
        working_directory=row["working_directory"],
        harness=row["harness"],
        model=row["model"],
        effort=row["effort"],
    )


def new_session_draft(row: sqlite3.Row) -> NewSessionDraftRow:
    return NewSessionDraftRow(
        working_directory=row["working_directory"],
        text=row["text"],
        sequence=row["sequence"],
    )


def push_subscription(row: sqlite3.Row) -> PushSubscriptionRow:
    return PushSubscriptionRow(
        endpoint=row["endpoint"],
        public_key=row["public_key"],
        authentication_secret=row["authentication_secret"],
        device_id=row["device_id"],
        device_label=row["device_label"],
        created_at=row["created_at"],
    )


def push_signing_key(row: sqlite3.Row) -> PushSigningKeyRow:
    return PushSigningKeyRow(
        id=row["id"],
        private_key_pem=row["private_key_pem"],
        public_key=row["public_key"],
    )


def upload(row: sqlite3.Row) -> UploadRow:
    return UploadRow(
        upload_id=row["upload_id"],
        session_id=row["session_id"],
        name=row["name"],
        media_type=row["media_type"],
        byte_size=row["byte_size"],
        stored_path=row["stored_path"],
        created_at=row["created_at"],
    )


def error(row: sqlite3.Row) -> ErrorRow:
    return ErrorRow(
        id=row["id"],
        ts=row["ts"],
        session_id=row["session_id"],
        script=row["script"],
        func=row["func"],
        traceback=row["traceback"],
        context=row["context"],
        pid=row["pid"],
    )


def state_file(row: sqlite3.Row) -> StateFileRow:
    return StateFileRow(
        id=row["id"],
        ts=row["ts"],
        session_id=row["session_id"],
        path=row["path"],
        action=row["action"],
        content=row["content"],
        script=row["script"],
        pid=row["pid"],
    )


def spawn(row: sqlite3.Row) -> SpawnRow:
    return SpawnRow(
        id=row["id"],
        ts=row["ts"],
        session_id=row["session_id"],
        parent_script=row["parent_script"],
        child_pid=row["child_pid"],
        argv=row["argv"],
        purpose=row["purpose"],
    )


def stream(row: sqlite3.Row) -> StreamRow:
    return StreamRow(
        id=row["id"],
        session_id=row["session_id"],
        kind=row["kind"],
        agent_id=row["agent_id"],
        task_id=row["task_id"],
        src_path=row["src_path"],
        pid=row["pid"],
        started_at=row["started_at"],
        ended_at=row["ended_at"],
        end_reason=row["end_reason"],
        lines_emitted=row["lines_emitted"],
    )




def session_data(row: sqlite3.Row) -> SessionDataRow:
    return SessionDataRow(
        session_id=row["session_id"],
        revision=row["revision"],
        payload=row["payload"],
    )


def session_data_actor(row: sqlite3.Row) -> SessionDataActorRow:
    return SessionDataActorRow(
        session_id=row["session_id"],
        actor_id=row["actor_id"],
        revision=row["revision"],
        payload=row["payload"],
    )


def session_entry(row: sqlite3.Row) -> SessionEntryRow:
    return SessionEntryRow(
        cursor=row["cursor"],
        entry_id=row["entry_id"],
        session_id=row["session_id"],
        entry_type=row["entry_type"],
        actor_id=row["actor_id"],
        parent_actor_id=row["parent_actor_id"],
        turn_id=row["turn_id"],
        occurred_at=row["occurred_at"],
        summary=row["summary"],
        payload=row["payload"],
    )
