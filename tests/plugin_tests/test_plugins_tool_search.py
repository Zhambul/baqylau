# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude search tool tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.content import TextContent
from domain.event_resource import (
    SearchPerformed,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue, text_of

if TYPE_CHECKING:
    from harness.models.raw_events import (
        RawEvent,
        TranslationResult,
    )

type CodexTranslationDecisionCase = tuple[dict[str, JsonValue], str]


def _claude_search_result_event() -> RawEvent:
    return raw_event(
        {
            fixture.TYPE_FIELD: fixture.USER,
            fixture.UUID_FIELD: "tool-result-one",
            fixture.MESSAGE_FIELD: {
                fixture.CONTENT_FIELD: [
                    {
                        fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                        fixture.TOOL_USE_ID_FIELD: "tool-search-one",
                        fixture.CONTENT_FIELD: [
                            {
                                fixture.TYPE_FIELD: "tool_reference",
                                fixture.TOOL_NAME_FIELD: fixture.WEB_SEARCH_NAME,
                            },
                        ],
                    },
                ],
            },
        },
        harness=CLAUDE_CODE_HARNESS,
        source_type=fixture.TRANSCRIPT_SOURCE,
        raw_event_id="tool-result",
    )


def test_claude_search_is_one_fact_holding_both() -> None:
    """Verify claude search is one fact holding both its query and its result.

    A search has no life between asking and answering that anyone reads, so
        the call alone is not a fact — it is remembered, and the result carries
        both halves. The result text is rendered readably from its native blocks
        (here a `tool_reference`, which is how ToolSearch answers).
    """
    translator = ClaudeCanonicalTranslator()
    call = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                            fixture.ID_FIELD: "tool-search-one",
                            fixture.NAME_FIELD: fixture.TOOL_SEARCH_NAME,
                            fixture.INPUT_FIELD: {
                                fixture.QUERY_FIELD: fixture.SELECT_WEB_SEARCH,
                                "max_results": 1,
                            },
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="tool-search",
        ),
    )
    assert call.decision == fixture.IGNORED_NONSEMANTIC
    hook_result = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "tool-search-one",
                fixture.TOOL_NAME_FIELD: fixture.TOOL_SEARCH_NAME,
                fixture.TOOL_INPUT_FIELD: {
                    fixture.QUERY_FIELD: fixture.SELECT_WEB_SEARCH,
                    "max_results": 1,
                },
                fixture.TOOL_RESPONSE_FIELD: {
                    "matches": [fixture.WEB_SEARCH_NAME],
                    fixture.QUERY_FIELD: fixture.SELECT_WEB_SEARCH,
                    "total_deferred_tools": 34,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="tool-search-hook-result",
        ),
    )
    result = translator.translate(_claude_search_result_event())
    performed = payloads(result, SearchPerformed)[0].payload
    _assert_search_delivery_convergence(hook_result, result, performed)
    _assert_tool_search_content(performed)


def _assert_search_delivery_convergence(
    hook_result: TranslationResult,
    result: TranslationResult,
    performed: SearchPerformed,
) -> None:
    """Verify hook and transcript deliveries converge on one search fact."""
    assert payloads(hook_result, SearchPerformed)[0].payload == performed
    assert hook_result.canonical_events[0].event_id == result.canonical_events[0].event_id


def _assert_tool_search_content(performed: SearchPerformed) -> None:
    """Verify the query and result of a tool search."""
    assert performed.tool == fixture.TOOL_SEARCH_NAME
    assert performed.query == TextContent(fixture.SELECT_WEB_SEARCH)
    assert text_of(performed.result) == "→ loaded tool: WebSearch"
    assert performed.outcome == fixture.SUCCEEDED


def test_claude_tool_search_keeps_explicit_empty() -> None:
    """Verify claude tool search keeps an explicit empty result."""
    translator = ClaudeCanonicalTranslator()
    result = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "empty-tool-search",
                fixture.TOOL_NAME_FIELD: fixture.TOOL_SEARCH_NAME,
                fixture.TOOL_INPUT_FIELD: {fixture.QUERY_FIELD: "select:MissingTool"},
                fixture.TOOL_RESPONSE_FIELD: {
                    "matches": [],
                    fixture.QUERY_FIELD: "select:MissingTool",
                    "total_deferred_tools": 34,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="empty-tool-search-result",
        ),
    )

    performed = payloads(result, SearchPerformed)[0].payload
    assert performed.query == TextContent("select:MissingTool")
    assert performed.result == TextContent("No matching tools.")
    assert performed.outcome == fixture.SUCCEEDED
