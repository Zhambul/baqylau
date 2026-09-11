# Copyright (c) 2026 Zhambyl Yermagambet
"""Background hook output tests."""

import base64
from pathlib import Path

import pytest

from domain import event_shell, ids as domain_ids
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.claude_code.hooks import foreground as claude_foreground, gateway as claude_hooks
from harness.models.session import (
    Session,
)
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import (
    support_events,
    support_hooks,
    support_runtime,
    support_storage,
    support_terminal,
    support_values,
    vocabulary as fixture,
)
from tests.plugin_tests.hook_background_command_support import background_command_fixture, deliver_backgrounded_hook
from tests.plugin_tests.hook_background_support import assert_background_command_continues, finish_background_output
from tests.plugin_tests.hook_common_support import PRIMARY_SESSION, encoded_json_document, tick_interpreter

FOREGROUND_CHUNK_ORDINAL = 3


def test_claude_bg_output_streams_into_operation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify claude background output streams into the operation."""
    monkeypatch.setattr(claude_foreground, "BACKGROUND_OUTPUT_ROOT", str(tmp_path / "native"))
    monkeypatch.setenv(fixture.BAQYLAU_DATA_DIR_ENV, str(tmp_path / fixture.DATA_FIELD))
    output_path, document = support_terminal.background_post_tool_document(
        tmp_path / "native", session_id=fixture.SESSION_ONE_ID,
    )
    document[fixture.TRANSCRIPT_PATH] = str(tmp_path / fixture.SESSION_ONE_JSONL_PATH)

    support_hooks.deliver_hook(claude_hooks.ClaudeHookGateway(), encoded_json_document(document))

    runtime, interpreter = support_runtime.interpreting_runtime(tmp_path / fixture.DATA_FIELD / fixture.MAIN_DB_PATH)
    runtime.register(
        CLAUDE_CODE_HARNESS,
        Session(
            PRIMARY_SESSION,
            domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
            str(tmp_path / fixture.SESSION_ONE_JSONL_PATH),
            fixture.WORK_PATH,
            harness_process_id=fixture.FIRST_SHELL_PROCESS_ID,
        ),
    )
    interpreter.tick()  # translates the directive; the reaction starts the following
    output_path.write_bytes(b"1\n2\n3\n")  # the job keeps writing
    tick_interpreter(interpreter, 2)

    assert (
        support_storage.shell_output_text(runtime, PRIMARY_SESSION, domain_ids.ShellId(fixture.BACKGROUND_OP_ONE))
        == "1\n2\n3\n"
    )

    # the session's end is the background following's end: tail captured, row
    # gone, the NATIVE file untouched
    finish_background_output(runtime, interpreter, output_path)


def test_command_backgrounded_mid_run_keeps_its(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The whole point of the fact, end to end through the real interpreter.

    A foreground command is followed `until="operation_finished"`, and ctrl+b makes
    that finish arrive while the command runs on. Unhandled, the next tick drained
    the row, removed it, and UNLINKED the tee file the process was still writing
    to — output gone, and no exception anywhere to notice it by.
    """
    background_fixture = background_command_fixture(monkeypatch, tmp_path)
    deliver_backgrounded_hook(background_fixture.hook.gateway, background_fixture.hook.document)
    background_fixture.interpreter.tick()
    assert_background_command_continues(
        background_fixture.runtime,
        background_fixture.interpreter,
        fixture.SESSION_ONE_ID,
        "op-one",
        background_fixture.tee_path,
    )


def test_claude_fg_output_is_canon_append() -> None:
    """Verify claude foreground output is canonical append progress."""
    content = b"first line\nsecond line\n"
    translation = ClaudeCanonicalTranslator().translate(
        support_events.raw_event(
            {
                "shell_id": fixture.COMMAND_ONE,
                "ordinal": FOREGROUND_CHUNK_ORDINAL,
                "stream": fixture.OUTPUT_FIELD,
                "content_base64": base64.b64encode(content).decode("ascii"),
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.FOREGROUND_OUTPUT_ID,
            raw_event_id="foreground-one",
        ),
    )

    progress = support_events.payloads(translation, event_shell.ShellProgressed)[0].payload
    assert progress.shell_id == fixture.COMMAND_ONE
    assert progress.ordinal == FOREGROUND_CHUNK_ORDINAL
    assert progress.mode == "append"
    assert support_values.text_of(progress.content) == content.decode()


def test_claude_bg_launch_stub_is_not_progress() -> None:
    """Verify claude background launch stub is not progress.

    The 'Command running in background with ID …' tool_result is boilerplate,
        and its REPLACE mode wiped watch chunks that committed first. The finish
        fact still converges from the hook evidence.
    """
    stub = (
        "Command running in background with ID: btk9y72c9. Output is being "
        "written to: /tmp/task.output. You will be notified when it completes."
    )
    translation = ClaudeCanonicalTranslator().translate(
        support_events.raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "background-result",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                            fixture.TOOL_USE_ID_FIELD: "background-op",
                            fixture.CONTENT_FIELD: stub,
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="background-stub",
        ),
    )

    assert not support_events.payloads(translation, event_shell.ShellProgressed)
    assert translation.decision == fixture.IGNORED_NONSEMANTIC
