# Copyright (c) 2026 Zhambyl Yermagambet
"""Redirected shell output tests."""

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from domain import event_shell, ids as domain_ids
from engine.interpret.loop import Interpreter
from harness.impl.claude_code.hooks import gateway as claude_hooks
from harness.models.session import (
    Session,
)
from tests.canonical_runtime import CanonicalRuntime
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import support_hooks, support_runtime, support_storage, vocabulary as fixture
from tests.plugin_tests.hook_common_support import PRIMARY_SESSION, decoded_output_content

REDIRECTED_OUTPUT_EVENT_COUNT = 2


def test_claude_fg_bytes_flow_through_raw_audit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify claude foreground bytes flow through raw audit into operation projection."""
    monkeypatch.setenv(fixture.CLAUDE_CONFIG_DIR_ENV, str(tmp_path / fixture.CLAUDE))
    monkeypatch.setenv(fixture.BAQYLAU_DATA_DIR_ENV, str(tmp_path / "application"))
    document = {
        fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
        fixture.TRANSCRIPT_PATH: str(tmp_path / fixture.SESSION_ONE_JSONL_PATH),
        fixture.CWD_FIELD: str(tmp_path),
        fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
        fixture.HOOK_EVENT_ID_FIELD: "pre-command-one",
        fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
        fixture.TOOL_USE_ID_FIELD: fixture.COMMAND_ONE,
        fixture.TOOL_INPUT_FIELD: {fixture.COMMAND_FIELD: "printf hello"},
    }

    assert b"updatedInput" in support_hooks.deliver_hook(
        claude_hooks.ClaudeHookGateway(),
        json.dumps(document).encode(),
    )

    runtime, interpreter = support_runtime.interpreting_runtime(tmp_path / "application" / fixture.MAIN_DB_PATH)
    runtime.register(
        CLAUDE_CODE_HARNESS,
        Session(
            PRIMARY_SESSION,
            domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
            str(tmp_path / fixture.SESSION_ONE_JSONL_PATH),
            str(tmp_path),
            harness_process_id=fixture.THIRD_SHELL_PROCESS_ID,
        ),
    )
    interpreter.tick()  # translates the directive; the reaction starts the following
    output_sources = runtime.shell_output.find_for_session(PRIMARY_SESSION)
    assert len(output_sources) == 1
    Path(output_sources[0].source_path).write_bytes(b"hello\n")
    interpreter.tick()  # pulls the chunk and translates it

    assert (
        support_storage.shell_output_text(runtime, PRIMARY_SESSION, domain_ids.ShellId(fixture.COMMAND_ONE))
        == "hello\n"
    )
    foreground_evidence = [
        row
        for row in runtime.raw_event_audits.audits_for_session(
            domain_ids.SessionId(fixture.SESSION_ONE_ID),
        )
        if row.raw_event.source_type == fixture.FOREGROUND_OUTPUT_ID
    ]
    assert len(foreground_evidence) == 1
    assert decoded_output_content(foreground_evidence[0].raw_event.payload) == b"hello\n"


def _deliver_redirected_files_hook(tmp_path: Path, first: Path, second: Path) -> None:
    document = {
        fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
        fixture.TRANSCRIPT_PATH: str(tmp_path / fixture.SESSION_ONE_JSONL_PATH),
        fixture.CWD_FIELD: str(tmp_path),
        fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
        fixture.HOOK_EVENT_ID_FIELD: "pre-background-one",
        fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
        fixture.TOOL_USE_ID_FIELD: fixture.BACKGROUND_ONE,
        fixture.TOOL_INPUT_FIELD: {
            fixture.COMMAND_FIELD: (
                f"deploy > {first} 2>&1; echo done >> {first}; printf pipe | tee {second} >/dev/null"
            ),
            fixture.RUN_IN_BACKGROUND_FIELD: True,
        },
    }
    support_hooks.deliver_hook(claude_hooks.ClaudeHookGateway(), json.dumps(document).encode())


@dataclass(frozen=True)
class _RedirectedFilesFixture:
    runtime: CanonicalRuntime
    interpreter: Interpreter
    first: Path
    second: Path


def _redirected_files_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> _RedirectedFilesFixture:
    application = tmp_path / "application"
    first = tmp_path / "deploy.log"
    second = tmp_path / "pipe.log"
    monkeypatch.setenv(fixture.BAQYLAU_DATA_DIR_ENV, str(application))
    _deliver_redirected_files_hook(tmp_path, first, second)
    runtime, interpreter = support_runtime.interpreting_runtime(application / fixture.MAIN_DB_PATH)
    runtime.register(
        CLAUDE_CODE_HARNESS,
        Session(
            domain_ids.SessionId(fixture.SESSION_ONE_ID),
            domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
            str(tmp_path / fixture.SESSION_ONE_JSONL_PATH),
            str(tmp_path),
            harness_process_id=fixture.THIRD_SHELL_PROCESS_ID,
        ),
    )
    return _RedirectedFilesFixture(runtime, interpreter, first, second)


def test_claude_several_redirected_files_flow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Verify claude several redirected files flow into one background shell."""
    redirected_fixture = _redirected_files_fixture(monkeypatch, tmp_path)

    redirected_fixture.interpreter.tick()
    followings = redirected_fixture.runtime.shell_output.find_for_session(domain_ids.SessionId(fixture.SESSION_ONE_ID))
    assert [shell_output.source_path for shell_output in followings] == [
        str(redirected_fixture.first),
        str(redirected_fixture.second),
    ]
    redirected_fixture.first.write_text("deploy-output\nexit=0\n")
    redirected_fixture.second.write_text("pipe-output\n")
    redirected_fixture.interpreter.tick()

    output = support_storage.shell_output_text(
        redirected_fixture.runtime,
        domain_ids.SessionId(fixture.SESSION_ONE_ID),
        domain_ids.ShellId(fixture.BACKGROUND_ONE),
    )
    assert "deploy-output\nexit=0\n" in output
    assert "pipe-output\n" in output
    progressed = [
        event
        for event in redirected_fixture.runtime.store.page_from(0, 100)
        if isinstance(event.payload, event_shell.ShellProgressed)
        and event.payload.shell_id == domain_ids.ShellId(fixture.BACKGROUND_ONE)
    ]
    assert len(progressed) == REDIRECTED_OUTPUT_EVENT_COUNT
    assert len({event.event_id for event in progressed}) == len(progressed)
