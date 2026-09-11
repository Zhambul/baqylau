# Copyright (c) 2026 Zhambyl Yermagambet
"""Support for background hook tests."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from domain import event_base, event_session, event_shell, ids, outcomes
from engine.interpret.loop import Interpreter
from engine.interpret.reactions import (
    ShellOutputCanonicalEventReaction,
)
from harness.impl.claude_code.hooks import gateway as claude_hooks
from harness.models.session import (
    Session,
)
from tests.canonical_runtime import CanonicalRuntime
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import support_hooks, support_storage, vocabulary as fixture
from tests.plugin_tests.hook_common_support import PRIMARY_SESSION, encoded_json_document, tick_interpreter


def finish_background_output(
    runtime: CanonicalRuntime,
    interpreter: Interpreter,
    output_path: Path,
) -> None:
    """Finish a session and verify that its background output drains."""
    output_path.write_bytes(b"1\n2\n3\n4\n")
    finish = event_base.CanonicalEvent(
        ids.CanonicalEventId("session-finish"),
        PRIMARY_SESSION,
        ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
        None,
        None,
        CLAUDE_CODE_HARNESS,
        fixture.SHELL_SESSION_FINISH_TIME,
        None,
        None,
        event_session.SessionFinished(outcomes.Outcome.SUCCEEDED, None),
    )
    ShellOutputCanonicalEventReaction(runtime.shell_output, runtime.recorder).react(finish)
    assert not runtime.shell_output.find_for_session(PRIMARY_SESSION)
    assert output_path.exists()
    interpreter.tick()
    assert (
        support_storage.shell_output_text(runtime, PRIMARY_SESSION, ids.ShellId(fixture.BACKGROUND_OP_ONE))
        == "1\n2\n3\n4\n"
    )


def assert_background_command_continues(
    runtime: CanonicalRuntime,
    interpreter: Interpreter,
    session_id: str,
    shell_id: str,
    tee_path: Path,
) -> None:
    """Verify that a background command keeps its following and output."""
    survived = runtime.shell_output.find_for_session(ids.SessionId(session_id))
    assert len(survived) == 1, "the following was ended by the launch's finish"
    assert survived[0].until == fixture.SESSION_FINISHED_ID
    assert tee_path.exists(), "the file the command is still writing to was unlinked"
    tee_path.write_bytes(b"working\ndone\n")
    tick_interpreter(interpreter, 2)
    assert support_storage.shell_output_text(
        runtime, ids.SessionId(session_id), ids.ShellId(shell_id),
    ).endswith(
        fixture.DONE_TEXT,
    )
    _assert_background_events(runtime, ids.SessionId(session_id), ids.ShellId(shell_id))


def _assert_background_events(
    runtime: CanonicalRuntime,
    session_id: ids.SessionId,
    shell_id: ids.ShellId,
) -> None:
    backgrounded = support_storage.stored_payloads(
        runtime, session_id, event_shell.ShellBackgrounded,
    )
    assert [fact.shell_id for fact in backgrounded] == [shell_id]
    assert not support_storage.stored_payloads(
        runtime, session_id, event_shell.ShellOutputFinished,
    )


def start_foreground_following(
    runtime: CanonicalRuntime,
    interpreter: Interpreter,
    session_id: str,
) -> Path:
    """Start the interpreter and check its foreground output subscription.

    Returns:
        The source path for the single foreground output subscription.

    """
    interpreter.tick()
    following = runtime.shell_output.find_for_session(ids.SessionId(session_id))
    assert len(following) == 1
    assert following[0].until == "shell_finished"
    return Path(following[0].source_path)


def register_background_session(runtime: CanonicalRuntime, session_id: str, transcript_path: str) -> None:
    """Register the Claude session for a backgrounding test."""
    runtime.register(
        CLAUDE_CODE_HARNESS,
        Session(
            ids.SessionId(session_id),
            ids.ActorId(f"{session_id}:lead"),
            transcript_path,
            fixture.WORK_PATH,
            harness_process_id=fixture.SECOND_SHELL_PROCESS_ID,
        ),
    )


@dataclass(frozen=True)
class BackgroundHookFixture:
    """Hold the hook gateway, submitted document, and transcript path."""

    gateway: claude_hooks.ClaudeHookGateway
    document: Mapping[str, object]
    transcript_path: str


def foreground_background_hook(tmp_path: Path) -> BackgroundHookFixture:
    """Deliver a hook that starts a foreground shell command.

    Returns:
        The gateway, hook document, and transcript path for later test steps.

    """
    transcript_path = str(tmp_path / fixture.SESSION_ONE_JSONL_PATH)
    document = {
        fixture.SESSION_ID_FIELD: fixture.SESSION_ONE_ID,
        fixture.TRANSCRIPT_PATH: transcript_path,
        fixture.CWD_FIELD: fixture.WORK_PATH,
        fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
        fixture.HOOK_EVENT_ID_FIELD: "pretool-one",
        fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
        fixture.TOOL_USE_ID_FIELD: "op-one",
        fixture.TOOL_INPUT_FIELD: {fixture.COMMAND_FIELD: "sleep 30; echo done"},
    }
    gateway = claude_hooks.ClaudeHookGateway()
    support_hooks.deliver_hook(gateway, encoded_json_document(document))
    return BackgroundHookFixture(gateway, document, transcript_path)
