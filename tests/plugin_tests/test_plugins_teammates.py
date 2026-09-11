# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude Code teammate source translation tests."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from domain import event_actor, ids, messaging
from harness.impl.claude_code.canonical.sources import ClaudeTranscriptRawEventSource
from harness.impl.claude_code.hooks import gateway as claude_hooks
from harness.models.session import Session
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_hooks import deliver_hook
from tests.plugin_tests.support_runtime import interpreting_runtime
from tests.plugin_tests.support_storage import stored_payloads

CLAUDE_CODE_HARNESS = ids.HarnessName("claude_code")


def _write_teammate_transcripts(tmp_path: Path) -> tuple[Path, Path]:
    """Write the lead and teammate transcript fixtures.

    Returns:
        The lead and teammate transcript paths, in that order.

    """
    main_path = tmp_path / fixture.SESSION_ONE_JSONL_PATH
    main_path.write_text('{"type":"user","uuid":"lead"}\n')
    child_path = tmp_path / fixture.SESSION_ONE_ID / fixture.SUBAGENTS / "agent-worker-one.jsonl"
    child_path.parent.mkdir(parents=True)
    child_path.write_text(
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "worker-prompt",
                fixture.MESSAGE_FIELD: {fixture.CONTENT_FIELD: "inspect"},
            },
        )
        + "\n",
    )
    child_path.with_name("agent-worker-one.meta.json").write_text(json.dumps({"taskKind": "in_process_teammate"}))
    return main_path, child_path


def test_claude_teammate_hook_and_transcript(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify claude teammate hook and transcript share one actor identity."""
    main_path, child_path = _write_teammate_transcripts(tmp_path)
    monkeypatch.setenv(fixture.BAQYLAU_DATA_DIR_ENV, str(tmp_path / fixture.DATA_FIELD))
    deliver_hook(
        claude_hooks.ClaudeHookGateway(),
        json.dumps(
            {
                fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
                fixture.TRANSCRIPT_PATH: str(main_path),
                fixture.HOOK_EVENT_NAME_FIELD: fixture.SUBAGENT_START_HOOK,
                fixture.HOOK_EVENT_ID_FIELD: "worker-start",
                fixture.AGENT_ID_FIELD: fixture.WORKER_ONE_ID,
                "agent_type": "reviewer",
            },
        ).encode(),
    )
    runtime, interpreter = interpreting_runtime(tmp_path / fixture.DATA_FIELD / fixture.MAIN_DB_PATH)
    session = Session(
        ids.SessionId(fixture.SESSION_ONE_ID),
        ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        str(main_path),
        str(tmp_path),
    )
    runtime.register(CLAUDE_CODE_HARNESS, session)
    runtime.recorder.record(
        ClaudeTranscriptRawEventSource(
            replace(
                session.source_context,
                actor_id=ids.ActorId(fixture.WORKER_ONE_ID),
                parent_actor_id=session.lead_actor_id,
                source_reference=str(child_path),
            ),
            messaging.ActorRole.TEAMMATE,
        ).read(None),
    )
    interpreter.tick()

    assert [
        actor.role
        for actor in stored_payloads(
            runtime,
            ids.SessionId(fixture.SESSION_ONE_ID),
            event_actor.ActorStarted,
        )
        if actor.name == fixture.WORKER_ONE_ID
    ] == ["teammate"]
