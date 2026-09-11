# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex web tool tests."""

import json
from dataclasses import replace
from pathlib import Path

from domain import (
    content as domain_content,
    event_resource,
)
from harness.impl.codex.canonical import items as codex_items, rollout as codex_rollout
from harness.impl.codex.canonical.records import ExecRecord
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.collaboration_assertion_support import (
    assert_web_search_result,
)
from tests.plugin_tests.support_events import payloads, raw_event


def test_codex_web_tool_uses_shared_search(tmp_path: Path) -> None:
    """Verify codex web tool uses shared search vocabulary.

    One web tool covers search and fetch, and which one it was is decided by
        the fields it was called with — so the call names the tool and the result
        completes the fact.
    """
    call = (
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "web-one",
                    fixture.INPUT_FIELD: (
                        "const result = await tools.web__run("
                        '{search_query:[{q:"Bali weather"}],response_length:"short"}'
                        "); text(result);"
                    ),
                },
            },
        )
        + "\n"
    )
    rollout_path = tmp_path / fixture.ROLLOUT_JSONL_PATH
    rollout_path.write_text(call)
    rollout_source = str(rollout_path)
    translator = CodexCanonicalTranslator()
    assert (
        translator.translate(
            replace(
                raw_event(
                    json.loads(call),
                    harness=CODEX_HARNESS,
                    source_type=fixture.ROLLOUT_SOURCE,
                    raw_event_id="web-search",
                    source_position=fixture.ZERO_TEXT,
                ),
                source_name=rollout_source,
            ),
        ).canonical_events
        == ()
    )
    answered = translator.translate(
        replace(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                        fixture.CALL_ID_FIELD: "web-one",
                        fixture.OUTPUT_FIELD: [
                            {
                                fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                                fixture.TEXT_FIELD: fixture.SCRIPT_COMPLETED_OUTPUT_TEXT,
                            },
                            {
                                fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                                fixture.TEXT_FIELD: "26C and sunny",
                            },
                        ],
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="web-search-result",
                source_position=str(len(call.encode())),
            ),
            source_name=rollout_source,
        ),
    )

    assert_web_search_result(answered)


def test_codex_javascript_tool_scan_ignores() -> None:
    """Verify codex javascript tool scan ignores strings and comments."""
    calls = codex_items.js_tool_calls(
        'const patch = "tests mention tools.not_a_call({})";'
        "// tools.also_not_a_call({})\n"
        "/* tools.still_not_a_call({}) */"
        "text(await tools.apply_patch(patch));",
    )

    assert calls == (("apply_patch", "patch"),)


def test_codex_dynamic_shell_and_plan_tools_stay() -> None:
    """Verify codex dynamic shell and plan tools stay structured."""
    shell_record = codex_rollout.parse_line(
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "dynamic-shell",
                    fixture.INPUT_FIELD: (
                        'const cmd = "pytest -q";const result = await tools.exec_command({cmd});text(result.output);'
                    ),
                },
            },
        ),
    )
    plan_record = codex_rollout.parse_line(
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "dynamic-plan",
                    fixture.INPUT_FIELD: (
                        'const plan=[{step:"Verify tools",status:"in_progress"}];text(await tools.update_plan({plan}));'
                    ),
                },
            },
        ),
    )

    assert shell_record is not None
    # Dynamic single-command cells are owned by their exact native
    # CommandExecution completion rather than this wrapper expression.
    assert shell_record.kind == "covered_item"
    assert plan_record is not None
    assert plan_record.kind == "task_list"
    assert plan_record.tasks[0].step == "Verify tools"


def test_codex_legacy_shell_arguments_and_web() -> None:
    """Verify codex legacy shell arguments and web actions remain parseable."""
    shell_record = codex_rollout.parse_line(
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_ID,
                    fixture.NAME_FIELD: "exec_command",
                    fixture.CALL_ID_FIELD: "legacy-shell",
                    fixture.ARGUMENTS_FIELD: json.dumps(
                        {
                            "cmd": fixture.PRINT_DIRECTORY_COMMAND,
                            "workdir": fixture.WORK_PATH,
                            "yield_time_ms": 1000,
                            "max_output_tokens": 12000,
                            "sandbox_permissions": "require_escalated",
                            "justification": "read remote refs",
                            "prefix_rule": ["git", "fetch"],
                        },
                    ),
                },
            },
        ),
    )
    search_record = codex_rollout.parse_line(
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: "web_search_end",
                    fixture.CALL_ID_FIELD: "legacy-find",
                    fixture.QUERY_FIELD: "needle in page",
                    "action": {
                        fixture.TYPE_FIELD: "find_in_page",
                        fixture.URL_FIELD: fixture.HTTPS_EXAMPLE_COM_URL,
                        "pattern": "needle",
                        "queries": ["needle in page"],
                    },
                },
            },
        ),
    )

    assert isinstance(shell_record, ExecRecord)
    assert shell_record.cmd == fixture.PRINT_DIRECTORY_COMMAND
    assert search_record is not None
    assert search_record.kind == "search"
    assert search_record.query == "needle in page"


def test_codex_notify_tool_is_known_non_feed() -> None:
    """Verify codex notify tool is known non feed activity."""
    translation = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "notify",
                    fixture.INPUT_FIELD: "text(await tools.notify({}));",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="notify-call",
        ),
    )

    assert translation.canonical_events == ()
    assert translation.decision == fixture.IGNORED_NONSEMANTIC


def test_codex_nested_exec_wrapper_is_known() -> None:
    """Verify codex nested exec wrapper is known transport noise."""
    record = codex_rollout.parse_line(
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "nested-exec",
                    fixture.INPUT_FIELD: "const value = await tools.exec(envelope);text(value);",
                },
            },
        ),
    )

    assert record is not None
    assert record.kind == "empty"


def test_codex_legacy_direct_web_run_keeps_its() -> None:
    """Verify codex legacy direct web run keeps its result."""
    translator = CodexCanonicalTranslator()
    opened = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_ID,
                    fixture.NAME_FIELD: "run",
                    fixture.CALL_ID_FIELD: "legacy-web",
                    fixture.ARGUMENTS_FIELD: json.dumps(
                        {"search_query": [{"q": fixture.EXAMPLE_DOMAIN_TEXT}]},
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="legacy-web-call",
        ),
    )
    answered = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.FUNCTION_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "legacy-web",
                    fixture.OUTPUT_FIELD: "Example Domain result",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="legacy-web-result",
        ),
    )

    assert opened.decision == fixture.IGNORED_NONSEMANTIC
    performed = payloads(answered, event_resource.SearchPerformed)[0].payload
    assert performed.query == domain_content.TextContent(fixture.EXAMPLE_DOMAIN_TEXT)
    assert performed.result == domain_content.TextContent("Example Domain result")
