# Copyright (c) 2026 Zhambyl Yermagambet
"""Hook recording tests."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from domain import event_session, event_shell, ids as domain_ids, work_state
from harness.impl.claude_code.hooks import foreground as claude_foreground, gateway as claude_hooks
from tests.canonical_runtime import CanonicalRuntime
from tests.plugin_tests import support_hooks, support_runtime, support_storage, vocabulary as fixture
from tests.plugin_tests.hook_common_support import encoded_json_document, receive_claude_hook
from tests.plugin_tests.hook_recording_support import (
    assert_recorded_claude_audit,
    assert_recorded_claude_session,
    assert_recorded_codex_audit,
    assert_recorded_codex_session,
    hook_recording_fixture,
)


def test_hooks_record_exact_raw_bytes_and_both(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify hooks record exact raw bytes and both sessions are born from them."""
    recording_fixture = hook_recording_fixture(monkeypatch, tmp_path)
    assert_recorded_claude_session(recording_fixture)
    assert_recorded_codex_session(recording_fixture)
    assert_recorded_claude_audit(recording_fixture)
    assert_recorded_codex_audit(recording_fixture)


def _launch_selection_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> CanonicalRuntime:
    monkeypatch.setenv(fixture.BAQYLAU_DATA_DIR_ENV, tmp_path.as_posix())
    start_payload = json.dumps(
        {
            fixture.SESSION_ID_FIELD: fixture.CLAUDE_SESSION_ID,
            fixture.TRANSCRIPT_PATH: fixture.WORK_CLAUDE_JSONL_PATH,
            fixture.CWD_FIELD: fixture.WORK_PATH,
            fixture.HOOK_EVENT_NAME_FIELD: fixture.SESSION_START_HOOK,
            fixture.HOOK_EVENT_ID_FIELD: "start-1",
        },
    ).encode()
    stop_payload = json.dumps(
        {
            fixture.SESSION_ID_FIELD: fixture.CLAUDE_SESSION_ID,
            fixture.TRANSCRIPT_PATH: fixture.WORK_CLAUDE_JSONL_PATH,
            fixture.HOOK_EVENT_NAME_FIELD: fixture.STOP_HOOK,
            fixture.HOOK_EVENT_ID_FIELD: "stop-1",
        },
    ).encode()

    support_hooks.deliver_hook(
        claude_hooks.ClaudeHookGateway(),
        start_payload,
        launch_model=fixture.FABLE,
        launch_effort=fixture.HIGH,
    )
    # a later delivery still carries the inherited environment but is not a launch
    support_hooks.deliver_hook(
        claude_hooks.ClaudeHookGateway(),
        stop_payload,
        launch_model=fixture.FABLE,
        launch_effort=fixture.HIGH,
    )

    runtime, interpreter = support_runtime.interpreting_runtime(tmp_path / fixture.MAIN_DB_PATH)
    interpreter.tick()
    return runtime


def test_claude_launch_selections_reach_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify claude launch selections reach the summary from the hook environment."""
    runtime = _launch_selection_runtime(monkeypatch, tmp_path)

    launch_evidence = [
        audit_record
        for audit_record in runtime.raw_event_audits.audits_for_session(domain_ids.SessionId(fixture.CLAUDE_SESSION_ID))
        if audit_record.raw_event.source_type == "launch"
    ]
    assert len(launch_evidence) == 1
    assert launch_evidence[0].interpretation is not None
    model_changes = [
        recorded_event.event.payload
        for recorded_event in launch_evidence[0].interpretation.events
        if isinstance(recorded_event.event.payload, event_session.ModelChanged)
    ]
    # the environment carries the selection ALIAS; the native id arrives later,
    # on the first assistant record, as `reported_by_harness`
    assert model_changes[0].reason == "selected"
    assert model_changes[0].current.name == fixture.FABLE

    stored_models = support_storage.stored_payloads(
        runtime, domain_ids.SessionId(fixture.CLAUDE_SESSION_ID), event_session.ModelChanged,
    )
    stored_efforts = support_storage.stored_payloads(
        runtime, domain_ids.SessionId(fixture.CLAUDE_SESSION_ID), event_session.EffortChanged,
    )
    assert (
        stored_models[0].current.name,
        stored_efforts[0].current,
    ) == (fixture.FABLE, fixture.HIGH)


def test_hook_without_native_identity_uses_exact(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify hook without native identity uses the exact payload identity."""
    monkeypatch.setenv(fixture.BAQYLAU_DATA_DIR_ENV, tmp_path.as_posix())
    payload = (
        b'{"session_id":"claude-session","transcript_path":"/work/claude.jsonl",'
        b'"cwd":"/work","hook_event_name":"SessionStart"}'
    )

    support_hooks.deliver_hook(claude_hooks.ClaudeHookGateway(), payload)
    support_hooks.deliver_hook(claude_hooks.ClaudeHookGateway(), payload)

    runtime = CanonicalRuntime(str(tmp_path / fixture.MAIN_DB_PATH))
    evidence = runtime.raw_event_audits.audits_for_session(domain_ids.SessionId(fixture.CLAUDE_SESSION_ID))
    assert len(evidence) == 1
    payload_digest = hashlib.sha256(payload).hexdigest()
    assert str(evidence[0].raw_event.raw_event_id).endswith(payload_digest)


def test_hook_recording_preserves_native_child(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify hook recording preserves native child actor context."""
    monkeypatch.setenv(fixture.BAQYLAU_DATA_DIR_ENV, tmp_path.as_posix())
    payload = json.dumps(
        {
            fixture.SESSION_ID_FIELD: fixture.CLAUDE_SESSION_ID,
            fixture.TRANSCRIPT_PATH: fixture.WORK_CLAUDE_JSONL_PATH,
            fixture.HOOK_EVENT_NAME_FIELD: fixture.SUBAGENT_START_HOOK,
            fixture.HOOK_EVENT_ID_FIELD: fixture.CHILD_START_ID,
            fixture.AGENT_ID_FIELD: fixture.CHILD_ONE_ID,
        },
    ).encode()

    support_hooks.deliver_hook(claude_hooks.ClaudeHookGateway(), payload)

    runtime, interpreter = support_runtime.interpreting_runtime(tmp_path / fixture.MAIN_DB_PATH)
    interpreter.tick()
    evidence = runtime.raw_event_audits.audits_for_session(domain_ids.SessionId(fixture.CLAUDE_SESSION_ID))[0]
    assert evidence.raw_event.actor_id == domain_ids.ActorId(fixture.CHILD_ONE_ID)
    assert evidence.raw_event.parent_actor_id == domain_ids.ActorId("claude-session:lead")
    assert evidence.interpretation is not None
    interpreted_event = evidence.interpretation.events[0].event
    assert interpreted_event.actor_id == domain_ids.ActorId(fixture.CHILD_ONE_ID)


def test_claude_hook_returns_native_pretool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify claude hook returns native pretool output and an output location."""
    document = {
        fixture.SESSION_ID_FIELD: fixture.CLAUDE_SESSION_ID,
        fixture.TRANSCRIPT_PATH: fixture.WORK_CLAUDE_JSONL_PATH,
        fixture.CWD_FIELD: fixture.WORK_PATH,
        fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
        fixture.HOOK_EVENT_ID_FIELD: "pretool-one",
        fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
        fixture.TOOL_INPUT_FIELD: {fixture.COMMAND_FIELD: "echo hello"},
    }
    expected = b'{"hookSpecificOutput":{"updatedInput":{}}}\n'
    located = event_shell.ShellOutputLocated(
        shell_id=domain_ids.ShellId("pretool-one"),
        source_path="/work/out",
        chunk_source_type=fixture.FOREGROUND_OUTPUT_ID,
        delete_source=True,
        initial_size=0,
        initial_modified_at=0,
        wait_for_source_change=False,
        until=work_state.ShellFollowUntil.SESSION_FINISHED,
    )
    monkeypatch.setattr(
        claude_foreground,
        "prepare",
        lambda _hook_input: SimpleNamespace(reply=expected, locations=(located,)),
    )

    response = receive_claude_hook(document)

    assert response.reply == expected
    assert response.raw_events[0].payload == encoded_json_document(document)
    directive = response.raw_events[-1]
    assert directive.source_type == fixture.OUTPUT_LOCATION_ID
    assert json.loads(directive.payload)[fixture.SOURCE_PATH_FIELD] == "/work/out"
    assert json.loads(directive.payload)[fixture.UNTIL] == work_state.ShellFollowUntil.SESSION_FINISHED


def test_hook_row_carries_what_delivery_observed() -> None:
    """Verify the hook row carries what the delivery observed."""
    payload = json.dumps(
        {
            fixture.SESSION_ID_FIELD: fixture.CLAUDE_SESSION_ID,
            fixture.TRANSCRIPT_PATH: fixture.WORK_CLAUDE_JSONL_PATH,
            fixture.CWD_FIELD: fixture.WORK_PATH,
            fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
            fixture.HOOK_EVENT_ID_FIELD: "post-one",
            fixture.TOOL_NAME_FIELD: fixture.READ_TOOL,
        },
    ).encode()

    response = claude_hooks.ClaudeHookGateway().receive_hook(
        support_hooks.hook_request(
            payload,
            terminal_window_id=domain_ids.WindowId("1114"),
            harness_process_id=fixture.FIXTURE_PROCESS_ID,
            account_id=domain_ids.AccountId("legacy-account"),
            account_display_name="Account Two",
        ),
    )
    bare = claude_hooks.ClaudeHookGateway().receive_hook(support_hooks.hook_request(payload))

    hook_row = response.raw_events[0]
    assert (hook_row.terminal_window_id, hook_row.harness_process_id) == ("1114", fixture.FIXTURE_PROCESS_ID)
    assert (hook_row.account_id, hook_row.account_display_name) == (None, None)
    assert bare.raw_events[0].terminal_window_id is None
    # the flat fields ride the SAME row: no separate anchor observations exist
    assert [event.source_type for event in response.raw_events] == [fixture.HOOK_SOURCE]
