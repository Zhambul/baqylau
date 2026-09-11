# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude web tool tests."""

from __future__ import annotations

from domain.content import TextContent
from domain.event_resource import (
    SearchPerformed,
    WebFetched,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue

type CodexTranslationDecisionCase = tuple[dict[str, JsonValue], str]


def test_claude_web_search_hook_and_transcript() -> None:
    """Verify claude web search hook and transcript results converge readably.

    WebSearch's hook is first, but its structured response must render like
        the later transcript instead of winning the fact as an internal JSON dump.
    """
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                            fixture.ID_FIELD: "web-search-one",
                            fixture.NAME_FIELD: fixture.WEB_SEARCH_NAME,
                            fixture.INPUT_FIELD: {fixture.QUERY_FIELD: "IANA Example Domain reserved"},
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="web-search-call",
        ),
    )
    response: dict[str, JsonValue] = {
        fixture.QUERY_FIELD: "IANA Example Domain reserved",
        "results": [
            {
                fixture.TOOL_USE_ID_FIELD: "server-search-one",
                fixture.CONTENT_FIELD: [
                    {
                        fixture.TITLE_FIELD: fixture.EXAMPLE_DOMAIN_TEXT,
                        fixture.URL_FIELD: fixture.HTTPS_EXAMPLE_COM_URL,
                    },
                ],
            },
            "The Example Domain is reserved.",
        ],
        "durationSeconds": 0.5,
        "searchCount": 1,
    }
    hook_result = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "web-search-one",
                fixture.TOOL_NAME_FIELD: fixture.WEB_SEARCH_NAME,
                fixture.TOOL_INPUT_FIELD: {fixture.QUERY_FIELD: "IANA Example Domain reserved"},
                fixture.TOOL_RESPONSE_FIELD: response,
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="web-search-hook-result",
        ),
    )
    transcript_result = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                            fixture.TOOL_USE_ID_FIELD: "web-search-one",
                            fixture.CONTENT_FIELD: "vendor-formatted search result",
                        },
                    ],
                },
                fixture.TOOL_USE_RESULT: response,
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="web-search-transcript-result",
        ),
    )

    transcript_event = payloads(transcript_result, SearchPerformed)[0]
    assert payloads(hook_result, SearchPerformed)[0].event_id == transcript_event.event_id
    assert payloads(hook_result, SearchPerformed)[0].payload == transcript_event.payload
    assert payloads(hook_result, SearchPerformed)[0].payload.result == TextContent(
        'Web search results for query: "IANA Example Domain reserved"\n\n'
        "Links:\n- Example Domain — https://example.com\n\n"
        "The Example Domain is reserved.",
    )


def test_claude_web_fetch_hook_and_transcript() -> None:
    """WebFetch already exposes a direct result field; pin both arrival paths."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                            fixture.ID_FIELD: "web-fetch-one",
                            fixture.NAME_FIELD: "WebFetch",
                            fixture.INPUT_FIELD: {
                                fixture.URL_FIELD: fixture.HTTPS_EXAMPLE_COM_URL,
                                fixture.PROMPT_KIND: "Read the page",
                            },
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="web-fetch-call",
        ),
    )
    response: dict[str, JsonValue] = {
        "bytes": 14,
        fixture.CODE: 200,
        "codeText": "OK",
        fixture.RESULT: fixture.EXAMPLE_DOMAIN_TEXT,
        fixture.DURATION_MS_FIELD: 10,
        fixture.URL_FIELD: fixture.HTTPS_EXAMPLE_COM_URL,
    }
    hook_result = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "web-fetch-one",
                fixture.TOOL_NAME_FIELD: "WebFetch",
                fixture.TOOL_INPUT_FIELD: {
                    fixture.URL_FIELD: fixture.HTTPS_EXAMPLE_COM_URL,
                    fixture.PROMPT_KIND: "Read the page",
                },
                fixture.TOOL_RESPONSE_FIELD: response,
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="web-fetch-hook-result",
        ),
    )
    transcript_result = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                            fixture.TOOL_USE_ID_FIELD: "web-fetch-one",
                            fixture.CONTENT_FIELD: fixture.EXAMPLE_DOMAIN_TEXT,
                        },
                    ],
                },
                fixture.TOOL_USE_RESULT: response,
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="web-fetch-transcript-result",
        ),
    )

    transcript_event = payloads(transcript_result, WebFetched)[0]
    assert payloads(hook_result, WebFetched)[0].event_id == transcript_event.event_id
    assert payloads(hook_result, WebFetched)[0].payload == transcript_event.payload
    assert payloads(hook_result, WebFetched)[0].payload.result == TextContent(fixture.EXAMPLE_DOMAIN_TEXT)
