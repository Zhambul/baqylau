# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex resource tool tests."""

from __future__ import annotations

import json

from domain import (
    event_resource,
)
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of


def test_codex_node_repl_file_read_keeps_its_path() -> None:
    """Verify codex node repl file read keeps its path and content."""
    translator = CodexCanonicalTranslator()
    opened = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "read-one",
                    fixture.INPUT_FIELD: (
                        "const result = await tools.mcp__node_repl__js({"
                        'title:"Read README.md",code:`var fs = await import('
                        '"node:fs/promises"); var text = await fs.readFile('
                        '"${"/work/README.md"}", "utf8"); nodeRepl.write(text);`});'
                        "text(result);"
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="read-start",
        ),
    )
    answered = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "read-one",
                    fixture.OUTPUT_FIELD: json.dumps(
                        {
                            fixture.CONTENT_FIELD: [
                                {
                                    fixture.TYPE_FIELD: fixture.TEXT_FIELD,
                                    fixture.TEXT_FIELD: "# Guide\nBody\n",
                                },
                            ],
                            "isError": False,
                        },
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="read-result",
        ),
    )

    assert opened.canonical_events == ()
    accessed = payloads(answered, event_resource.FileAccessed)[0].payload
    assert accessed.path == "/work/README.md"
    assert accessed.action == "read"
    assert text_of(accessed.content) == "# Guide\nBody\n"


def test_codex_node_repl_file_read_accepts_plain() -> None:
    """Verify codex node repl file read accepts plain wrapper output."""
    translator = CodexCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "read-plain",
                    fixture.INPUT_FIELD: (
                        "const result = await tools.mcp__node_repl__js({"
                        'title:"Read README.md",code:`await fs.readFile('
                        '"/work/README.md", "utf8")`});text(result);'
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="read-plain-start",
        ),
    )
    answered = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "read-plain",
                    fixture.OUTPUT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                            fixture.TEXT_FIELD: "Script completed\nOutput:\n# Guide\nBody\n",
                        },
                    ],
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="read-plain-result",
        ),
    )

    accessed = payloads(answered, event_resource.FileAccessed)[0].payload
    assert accessed.path == "/work/README.md"
    assert text_of(accessed.content) == "# Guide\nBody"


def test_codex_node_repl_file_read_uses_native() -> None:
    """Verify codex node repl file read uses the native mcp failure state."""
    translator = CodexCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "read-failed",
                    fixture.INPUT_FIELD: (
                        "const r = await tools.mcp__node_repl__js({"
                        'title:"Read requested file",code:`var fs = await import('
                        '"node:fs/promises"); await fs.readFile('
                        '"/work/missing.txt", "utf8");`});'
                        'for (const c of r.content) if (c.type === "text") text(c.text);'
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="read-failed-start",
        ),
    )
    completion = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: fixture.MCP_TOOL_CALL_KIND,
                        fixture.ID_FIELD: "exec-native",
                        "server": "node_repl",
                        fixture.TOOL_KIND: "js",
                        fixture.ARGUMENTS_FIELD: {
                            fixture.TITLE_FIELD: "Read requested file",
                            fixture.CODE: 'await fs.readFile("/work/missing.txt", "utf8")',
                        },
                        "readOnlyHint": True,
                        fixture.STATUS_FIELD: fixture.FAILED,
                        fixture.RESULT: {
                            fixture.CONTENT_FIELD: [
                                {
                                    fixture.TYPE_FIELD: fixture.TEXT_FIELD,
                                    fixture.TEXT_FIELD: "ENOENT: no such file or directory",
                                },
                            ],
                            "isError": True,
                        },
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="read-failed-native-result",
        ),
    )
    answered = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "read-failed",
                    fixture.OUTPUT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                            fixture.TEXT_FIELD: "Script completed\nWall time 0.3 seconds\nOutput:\n",
                        },
                        {
                            fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                            fixture.TEXT_FIELD: "ENOENT: no such file or directory",
                        },
                    ],
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="read-failed-result",
        ),
    )

    assert completion.canonical_events == ()
    accessed = payloads(answered, event_resource.FileAccessed)[0].payload
    assert accessed.path == "/work/missing.txt"
    assert accessed.outcome == fixture.FAILED


def test_codex_browser_mcp_completion_is_named() -> None:
    """Verify codex browser mcp completion is a named expandable fact."""
    translated = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: fixture.MCP_TOOL_CALL_KIND,
                        fixture.ID_FIELD: "browser-refresh",
                        "server": "node_repl",
                        fixture.TOOL_KIND: "js",
                        fixture.ARGUMENTS_FIELD: {
                            fixture.TITLE_FIELD: "Refresh the fixture application",
                            fixture.CODE: "await fixtureTab.reload()",
                        },
                        fixture.STATUS_FIELD: fixture.COMPLETED,
                        fixture.RESULT: {
                            fixture.CONTENT_FIELD: [
                                {
                                    fixture.TYPE_FIELD: fixture.TEXT_FIELD,
                                    fixture.TEXT_FIELD: '- banner:\n  - link "baqylau"',
                                },
                            ],
                            "isError": False,
                            "_meta": {"codex/browserUse": True},
                        },
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="browser-refresh-completed",
        ),
    )

    interacted = payloads(translated, event_resource.BrowserInteracted)[0].payload
    assert interacted.action == "Refresh the fixture application"
    assert text_of(interacted.result) == '- banner:\n  - link "baqylau"'
    assert interacted.outcome == fixture.SUCCEEDED


def test_codex_builtin_resource_call_matches_its() -> None:
    """Verify codex builtin resource call matches its native mcp completion."""
    translator = CodexCanonicalTranslator()
    opened = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "resource-list",
                    fixture.INPUT_FIELD: ("const r = await tools.list_mcp_resources({});text(JSON.stringify(r));"),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="resource-list-start",
        ),
    )
    completed = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: fixture.MCP_TOOL_CALL_KIND,
                        fixture.ID_FIELD: "resource-list-native",
                        "server": fixture.CODEX_HARNESS,
                        fixture.TOOL_KIND: "list_mcp_resources",
                        fixture.STATUS_FIELD: fixture.COMPLETED,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="resource-list-completed",
        ),
    )
    answered = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "resource-list",
                    fixture.OUTPUT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                            fixture.TEXT_FIELD: 'Script completed\nOutput:\n{"resources":[]}',
                        },
                    ],
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="resource-list-result",
        ),
    )

    assert opened.decision == fixture.IGNORED_NONSEMANTIC
    assert completed.decision == fixture.IGNORED_NONSEMANTIC
    assert answered.decision == fixture.IGNORED_NONSEMANTIC


def test_codex_execution_introspection() -> None:
    """Verify codex execution introspection and mirrored items are known plumbing."""
    introspection = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "inspect-tools",
                    fixture.INPUT_FIELD: "text(ALL_TOOLS.filter(item => item.name));",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="inspect-tools",
        ),
    )
    completed_item = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: fixture.MCP_TOOL_CALL_KIND,
                        fixture.ID_FIELD: "mcp-one",
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="mcp-item",
        ),
    )
    compacted_item = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: "ContextCompaction",
                        fixture.ID_FIELD: fixture.COMPACT_ONE_ID,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="compaction-item",
        ),
    )

    assert introspection.decision == fixture.IGNORED_NONSEMANTIC
    assert completed_item.decision == fixture.IGNORED_NONSEMANTIC
    assert compacted_item.decision == fixture.IGNORED_NONSEMANTIC


def test_codex_v2_agent_transport_records() -> None:
    """Verify codex v2 agent transport records are known plumbing."""
    translator = CodexCanonicalTranslator()
    trigger = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: "inter_agent_communication_metadata",
                fixture.PAYLOAD_FIELD: {"trigger_turn": True},
            },
            harness=CODEX_HARNESS,
            source_type=fixture.CHILD_ROLLOUT_ID,
            raw_event_id="v2-trigger",
        ),
    )
    envelope = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: "agent_message",
                    fixture.ID_FIELD: "v2-message",
                    "author": "/root",
                    "recipient": "/root/worker",
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                            fixture.TEXT_FIELD: "Message Type: NEW_TASK",
                        },
                        {fixture.TYPE_FIELD: "encrypted_content", "encrypted_content": "ciphertext"},
                    ],
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.CHILD_ROLLOUT_ID,
            raw_event_id="v2-envelope",
        ),
    )

    assert trigger.canonical_events == ()
    assert trigger.decision == fixture.IGNORED_NONSEMANTIC
    assert envelope.canonical_events == ()
    assert envelope.decision == fixture.IGNORED_NONSEMANTIC
