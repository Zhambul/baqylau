# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude file convergence tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain import (
    event_resource,
    event_shell,
    ids as domain_ids,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.models.session import (
    Session,
)
from tests import canonical_runtime, storage_reads
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.collaboration_file_support import (
    started_file_translators,
    tool_finish_evidence,
)
from tests.plugin_tests.support_events import encoded_event, payloads, raw_event
from tests.plugin_tests.support_values import JsonValue, text_of

if TYPE_CHECKING:
    from pathlib import Path

CREATED_LINE_COUNT = 2
FAILED_SHELL_EXIT_CODE = 7


def test_claude_file_facts_converge_from_either() -> None:
    """Verify claude file facts converge from either evidence stream.

    A file's path is in the call and its diff is in the result, so the fact is
        built at result time from both. Either stream can carry it — the hook's own
        response, or the transcript's `toolUseResult` sidecar — and both spellings
        are the same fact.
    """
    response: dict[str, JsonValue] = {
        fixture.TYPE_FIELD: fixture.TEXT_FIELD,
        "file": {
            "filePath": fixture.WORK_A_PY_PATH,
            fixture.CONTENT_FIELD: "print(1)\n",
            "numLines": 1,
            "startLine": 1,
            "totalLines": 1,
        },
    }
    hook_translator, transcript_translator = started_file_translators()
    hook = hook_translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: fixture.TOOL_ONE_ID,
                fixture.TOOL_NAME_FIELD: fixture.READ_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.FILE_PATH_FIELD: fixture.WORK_A_PY_PATH},
                fixture.TOOL_RESPONSE_FIELD: response,
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="hook-finish",
        ),
    )
    transcript = transcript_translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "result-one",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                            fixture.TOOL_USE_ID_FIELD: fixture.TOOL_ONE_ID,
                            fixture.CONTENT_FIELD: "print(1)\n",
                        },
                    ],
                },
                fixture.TOOL_USE_RESULT: response,
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="transcript-finish",
        ),
    )

    assert encoded_event(payloads(hook, event_resource.FileAccessed)[0]) == encoded_event(
        payloads(transcript, event_resource.FileAccessed)[0],
    )
    assert text_of(payloads(hook, event_resource.FileAccessed)[0].payload.content) == "print(1)\n"


def test_claude_edit_completion_preserves_native() -> None:
    """Verify claude edit completion preserves the native structured patch."""
    translated = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "edit-one",
                fixture.TOOL_NAME_FIELD: "Edit",
                fixture.TOOL_INPUT_FIELD: {
                    fixture.FILE_PATH_FIELD: fixture.WORK_A_PY_PATH,
                    "old_string": "old",
                    "new_string": "new",
                },
                fixture.TOOL_RESPONSE_FIELD: {
                    "structuredPatch": [
                        {
                            "oldStart": 1,
                            "oldLines": 1,
                            "newStart": 1,
                            "newLines": 1,
                            "lines": ["-old", "+new"],
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="edit-finish",
        ),
    )

    file_event = payloads(translated, event_resource.FileAccessed)[0].payload
    assert file_event.lines_added == 1
    assert file_event.lines_removed == 1
    assert file_event.unified_diff == ("--- /work/a.py\n+++ /work/a.py\n@@ -1,1 +1,1 @@\n-old\n+new\n")


def test_claude_write_counts_created_content() -> None:
    """Verify claude write counts created content without a structured patch."""
    translated = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "write-one",
                fixture.TOOL_NAME_FIELD: "Write",
                fixture.TOOL_INPUT_FIELD: {
                    fixture.FILE_PATH_FIELD: fixture.WORK_A_PY_PATH,
                    fixture.CONTENT_FIELD: "first\nsecond\n",
                },
                fixture.TOOL_RESPONSE_FIELD: {fixture.CONTENT_FIELD: "File created successfully"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="write-finish",
        ),
    )

    file_event = payloads(translated, event_resource.FileAccessed)[0].payload
    assert file_event.lines_added == CREATED_LINE_COUNT
    assert file_event.lines_removed is None
    assert text_of(file_event.content) == "first\nsecond\n"


def test_claude_hook_and_transcript_tool_finish(tmp_path: Path) -> None:
    """Verify claude hook and transcript tool finish deduplicate transactionally."""
    evidence = tool_finish_evidence()

    store = canonical_runtime.CanonicalRuntime(str(tmp_path / fixture.MAIN_DB_PATH))
    store.register(
        CLAUDE_CODE_HARNESS,
        Session(
            domain_ids.SessionId(fixture.SESSION_ONE_ID),
            domain_ids.ActorId(fixture.SESSION_ONE_LEAD_ID),
            "fixture.jsonl",
            fixture.WORK_PATH,
        ),
    )
    store.record(evidence.hook_raw, fixture.ONE_TEXT, evidence.hook)
    accepted = store.record(evidence.transcript_raw, fixture.ONE_TEXT, evidence.transcript)
    assert evidence.hook_finished.event_id not in {event.event_id for event in accepted}
    committed = storage_reads.page_from(store.store, 0, 10)
    assert evidence.hook_finished.event_id in {event.event_id for event in committed}
    finished = store.store.find(evidence.hook_finished.event_id)
    assert finished is not None
    assert domain_ids.RawEventId("transcript-finish") in finished.raw_event_ids


def test_claude_failed_shell_exit_code_converges() -> None:
    """Verify claude failed shell exit code converges from hook and transcript."""
    hook_translator = ClaudeCanonicalTranslator()
    transcript_translator = ClaudeCanonicalTranslator()
    transcript_translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.UUID_FIELD: fixture.ASSISTANT_ONE,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                            fixture.ID_FIELD: "shell-failed",
                            fixture.NAME_FIELD: fixture.BASH_TOOL,
                            fixture.INPUT_FIELD: {fixture.COMMAND_FIELD: "exit 7"},
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="shell-failed-start",
        ),
    )
    hook = hook_translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: "PostToolUseFailure",
                fixture.TOOL_USE_ID_FIELD: "shell-failed",
                fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.COMMAND_FIELD: "exit 7"},
                fixture.ERROR: "Exit code 7\nexpected-error",
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="shell-failed-hook",
        ),
    )
    transcript = transcript_translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.UUID_FIELD: "shell-failed-result",
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                            fixture.TOOL_USE_ID_FIELD: "shell-failed",
                            fixture.CONTENT_FIELD: "Exit code 7\nexpected-error",
                            fixture.IS_ERROR: True,
                        },
                    ],
                },
                fixture.TOOL_USE_RESULT: "Error: Exit code 7\nexpected-error",
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="shell-failed-transcript",
        ),
    )

    assert payloads(hook, event_shell.ShellFinished)[0].payload.exit_code == FAILED_SHELL_EXIT_CODE
    assert encoded_event(
        payloads(hook, event_shell.ShellFinished)[0],
    ) == encoded_event(
        payloads(transcript, event_shell.ShellFinished)[0],
    )
