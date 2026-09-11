# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex batched web tests."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING

from domain import (
    content as domain_content,
    event_resource,
    event_shell,
)
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.collaboration_assertion_support import (
    assert_failed_resource_read,
    assert_web_open_result,
)
from tests.plugin_tests.support_events import payloads, raw_event

if TYPE_CHECKING:
    from pathlib import Path


def test_codex_batched_web_tool_keeps_shared() -> None:
    """Verify codex batched web tool keeps the shared wrapper result."""
    translator = CodexCanonicalTranslator()
    opened = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "batched-web",
                    fixture.INPUT_FIELD: (
                        'const command = await tools.exec_command({cmd:"pwd"});'
                        "const web = await tools.web__run("
                        '{search_query:[{q:"Example Domain"}]});'
                        "text(web);"
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="batched-web-call",
        ),
    )
    answered = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "batched-web",
                    fixture.OUTPUT_FIELD: "Script completed\nOutput:\nExample Domain result",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="batched-web-result",
        ),
    )

    assert payloads(opened, event_shell.ShellStarted)
    performed = payloads(answered, event_resource.SearchPerformed)[0].payload
    assert performed.query == domain_content.TextContent(fixture.EXAMPLE_DOMAIN_TEXT)
    assert performed.result == domain_content.TextContent("Example Domain result")


def test_codex_batched_image_views_keep_every() -> None:
    """Verify codex batched image views keep every file fact."""
    translator = CodexCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "batched-images",
                    fixture.INPUT_FIELD: (
                        'image(await tools.view_image({path:"/work/one.png"}));'
                        'image(await tools.view_image({path:"/work/two.png"}));'
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="batched-images-call",
        ),
    )
    answered = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "batched-images",
                    fixture.OUTPUT_FIELD: "Script completed\nOutput:\nimages loaded",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="batched-images-result",
        ),
    )

    accessed = payloads(answered, event_resource.FileAccessed)
    assert [event.payload.path for event in accessed] == [
        "/work/one.png",
        "/work/two.png",
    ]


def test_codex_web_time_lookup_is_known_search() -> None:
    """Verify codex web time lookup is known search activity."""
    translator = CodexCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "time-lookup",
                    fixture.INPUT_FIELD: (
                        'const value = await tools.web__run({time:[{utc_offset:"+08:00"}]});text(value);'
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="time-lookup-call",
        ),
    )
    answered = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "time-lookup",
                    fixture.OUTPUT_FIELD: "21:30",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="time-lookup-result",
        ),
    )

    performed = payloads(answered, event_resource.SearchPerformed)[0].payload
    assert performed.tool == fixture.WEB_SEARCH_NAME
    assert performed.result == domain_content.TextContent("21:30")


def test_codex_web_extension_item_is_covered_copy() -> None:
    """Verify codex web extension item is the covered copy of the custom tool result."""
    translated = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.THREAD_ID_FIELD: fixture.SESSION_ONE_ID,
                    fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: "Extension",
                        fixture.KIND_FIELD: "web.search",
                        fixture.ID_FIELD: "exec-one",
                        fixture.QUERY_FIELD: "Bali weather",
                        "action": {fixture.TYPE_FIELD: "search", fixture.QUERY_FIELD: "Bali weather"},
                        "results": [{fixture.TYPE_FIELD: "text_result", fixture.TITLE_FIELD: "Weather"}],
                    },
                    "started_at_ms": 1,
                    "completed_at_ms": 2,
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="web-extension",
        ),
    )

    assert translated.canonical_events == ()
    assert translated.decision == fixture.IGNORED_NONSEMANTIC


def test_codex_web_open_uses_url_ref_as_fetched(tmp_path: Path) -> None:
    """Verify codex web open uses a URL ref as the fetched address."""
    call = (
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "web-open-one",
                    fixture.INPUT_FIELD: (
                        "const result = await tools.web__run("
                        '{open:[{ref_id:"https://example.com"}],response_length:"short"}'
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
    translator.translate(
        replace(
            raw_event(
                json.loads(call),
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="web-open",
                source_position=fixture.ZERO_TEXT,
            ),
            source_name=rollout_source,
        ),
    )
    answered = translator.translate(
        replace(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                        fixture.CALL_ID_FIELD: "web-open-one",
                        fixture.OUTPUT_FIELD: [
                            {
                                fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                                fixture.TEXT_FIELD: fixture.SCRIPT_COMPLETED_OUTPUT_TEXT,
                            },
                            {
                                fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                                fixture.TEXT_FIELD: fixture.EXAMPLE_DOMAIN_TEXT,
                            },
                        ],
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="web-open-result",
                source_position=str(len(call.encode())),
            ),
            source_name=rollout_source,
        ),
    )

    assert_web_open_result(answered)


def test_codex_failed_local_mcp_resource_read(tmp_path: Path) -> None:
    """Verify codex failed local mcp resource read is a failed file fact."""
    call = (
        json.dumps(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "resource-read-one",
                    fixture.INPUT_FIELD: (
                        "const result = await tools.read_mcp_resource("
                        '{server:"filesystem",uri:"file:///work/missing.txt"}); text(result);'
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
                    raw_event_id="resource-read",
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
                        fixture.CALL_ID_FIELD: "resource-read-one",
                        fixture.OUTPUT_FIELD: [
                            {
                                fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                                fixture.TEXT_FIELD: "Script failed\nOutput:\n",
                            },
                            {
                                fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                                fixture.TEXT_FIELD: "resource was not found",
                            },
                        ],
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="resource-read-result",
                source_position=str(len(call.encode())),
            ),
            source_name=rollout_source,
        ),
    )

    assert_failed_resource_read(answered)
