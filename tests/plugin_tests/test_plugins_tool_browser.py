# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude browser tool tests."""

from __future__ import annotations

import json

import pytest

from domain.content import TextContent
from domain.event_resource import (
    BrowserInteracted,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.claude_code.hooks import gateway as claude_hooks
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_hooks import hook_request
from tests.plugin_tests.support_values import JsonValue, text_of

type CodexTranslationDecisionCase = tuple[dict[str, JsonValue], str]


def test_claude_browser_mcp_result_is_named() -> None:
    """Verify claude browser mcp result is a named browser fact."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                            fixture.ID_FIELD: "browser-navigate",
                            fixture.NAME_FIELD: "mcp__claude-in-chrome__navigate",
                            fixture.INPUT_FIELD: {fixture.URL_FIELD: fixture.HTTPS_EXAMPLE_COM_URL},
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="browser-navigate-call",
        ),
    )
    hook_result = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "browser-navigate",
                fixture.TOOL_NAME_FIELD: "mcp__claude-in-chrome__navigate",
                fixture.TOOL_INPUT_FIELD: {fixture.URL_FIELD: fixture.HTTPS_EXAMPLE_COM_URL},
                fixture.TOOL_RESPONSE_FIELD: [
                    {fixture.TYPE_FIELD: fixture.TEXT_FIELD, fixture.TEXT_FIELD: "Example Domain loaded"},
                    {
                        fixture.TYPE_FIELD: fixture.IMAGE,
                        fixture.SOURCE: {
                            fixture.TYPE_FIELD: fixture.BASE64,
                            fixture.MEDIA_TYPE_FIELD: "image/jpeg",
                            fixture.DATA_FIELD: "binary-image-data",
                        },
                    },
                ],
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="browser-navigate-hook-result",
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
                            fixture.TOOL_USE_ID_FIELD: "browser-navigate",
                            fixture.CONTENT_FIELD: [
                                {
                                    fixture.TYPE_FIELD: fixture.TEXT_FIELD,
                                    fixture.TEXT_FIELD: "Example Domain loaded",
                                },
                                {
                                    fixture.TYPE_FIELD: fixture.IMAGE,
                                    fixture.SOURCE: {
                                        fixture.TYPE_FIELD: fixture.BASE64,
                                        fixture.MEDIA_TYPE_FIELD: "image/jpeg",
                                        fixture.DATA_FIELD: "binary-image-data",
                                    },
                                },
                            ],
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="browser-navigate-result",
        ),
    )

    hook_event = payloads(hook_result, BrowserInteracted)[0]
    transcript_event = payloads(transcript_result, BrowserInteracted)[0]
    assert hook_event.event_id == transcript_event.event_id
    assert hook_event.payload == transcript_event.payload
    assert hook_event.payload.action == "Navigate to https://example.com"
    assert hook_event.payload.result == TextContent("Example Domain loaded\n[image]")
    assert "binary-image-data" not in text_of(hook_event.payload.result)


def test_claude_future_chrome_verb_stays_browser() -> None:
    """Verify claude future chrome verb stays a browser fact."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                            fixture.ID_FIELD: "browser-future",
                            fixture.NAME_FIELD: ("mcp__claude-in-chrome__inspect_accessibility_tree"),
                            fixture.INPUT_FIELD: {},
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="browser-future-call",
        ),
    )
    translated = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.USER,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_RESULT_ID,
                            fixture.TOOL_USE_ID_FIELD: "browser-future",
                            fixture.CONTENT_FIELD: "Accessible page",
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="browser-future-result",
        ),
    )

    interacted = payloads(translated, BrowserInteracted)[0].payload
    assert interacted.action == "Inspect accessibility tree"
    assert interacted.result == TextContent("Accessible page")


def test_claude_accepts_browser_mcp_attribution() -> None:
    """Verify claude accepts browser mcp attribution fields."""
    translated = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TEXT_FIELD,
                            fixture.TEXT_FIELD: "The browser action finished.",
                        },
                    ],
                },
                "attributionMcpServer": "claude-in-chrome",
                "attributionMcpTool": "navigate",
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="browser-attribution",
        ),
    )

    assert translated.decision == fixture.TRANSLATED


def test_claude_chrome_permission_returns_session() -> None:
    """Verify claude chrome permission returns a session allow decision."""
    session_update = {
        fixture.TYPE_FIELD: "addRules",
        "rules": [
            {
                "toolName": "ClaudeInChromeDomain",
                "ruleContent": "example.com",
            },
        ],
        fixture.BEHAVIOR_FIELD: fixture.ALLOW,
        "destination": "session",
    }
    payload = json.dumps(
        {
            fixture.SESSION_ID_FIELD: fixture.CLAUDE_SESSION_ID,
            fixture.TRANSCRIPT_PATH: fixture.WORK_CLAUDE_JSONL_PATH,
            fixture.CWD_FIELD: fixture.WORK_PATH,
            fixture.HOOK_EVENT_NAME_FIELD: fixture.PERMISSION_REQUEST_HOOK,
            fixture.TOOL_NAME_FIELD: "mcp__claude-in-chrome__navigate",
            fixture.TOOL_INPUT_FIELD: {fixture.URL_FIELD: fixture.HTTPS_EXAMPLE_COM_URL},
            "permission_suggestions": [
                session_update,
                {
                    **session_update,
                    "destination": "localSettings",
                },
            ],
        },
    ).encode()

    response = claude_hooks.ClaudeHookGateway().receive_hook(hook_request(payload))

    assert json.loads(response.reply) == {
        fixture.HOOK_SPECIFIC_OUTPUT: {
            "hookEventName": fixture.PERMISSION_REQUEST_HOOK,
            "decision": {
                fixture.BEHAVIOR_FIELD: fixture.ALLOW,
                "updatedPermissions": [session_update],
            },
        },
    }
    assert response.raw_events[0].payload == payload


def test_claude_chrome_permission_with_mcp_server() -> None:
    """Claude 2.1.x names the MCP server on the permission request itself.

    A delivery this model refuses never reaches the reply, and the person
    sees Claude's own dialog for a tool Baqylau is supposed to answer.
    """
    payload = json.dumps(
        {
            fixture.SESSION_ID_FIELD: fixture.CLAUDE_SESSION_ID,
            fixture.TRANSCRIPT_PATH: fixture.WORK_CLAUDE_JSONL_PATH,
            fixture.CWD_FIELD: fixture.WORK_PATH,
            fixture.HOOK_EVENT_NAME_FIELD: fixture.PERMISSION_REQUEST_HOOK,
            fixture.TOOL_NAME_FIELD: "mcp__claude-in-chrome__computer",
            fixture.TOOL_INPUT_FIELD: {"action": "screenshot"},
            "mcp_server": {"name": "claude-in-chrome", "source": "dynamic"},
        },
    ).encode()

    response = claude_hooks.ClaudeHookGateway().receive_hook(hook_request(payload))

    assert json.loads(response.reply) == {
        fixture.HOOK_SPECIFIC_OUTPUT: {
            "hookEventName": fixture.PERMISSION_REQUEST_HOOK,
            "decision": {fixture.BEHAVIOR_FIELD: fixture.ALLOW},
        },
    }


def test_claude_chrome_permission_does_not() -> None:
    """Verify claude chrome permission does not persist a native allow rule."""
    payload = json.dumps(
        {
            fixture.SESSION_ID_FIELD: fixture.CLAUDE_SESSION_ID,
            fixture.TRANSCRIPT_PATH: fixture.WORK_CLAUDE_JSONL_PATH,
            fixture.CWD_FIELD: fixture.WORK_PATH,
            fixture.HOOK_EVENT_NAME_FIELD: fixture.PERMISSION_REQUEST_HOOK,
            fixture.TOOL_NAME_FIELD: "mcp__claude-in-chrome__computer",
            fixture.TOOL_INPUT_FIELD: {"action": "screenshot"},
            "permission_suggestions": [
                {
                    fixture.TYPE_FIELD: "addRules",
                    "rules": [{"toolName": "ClaudeInChromeDomain"}],
                    fixture.BEHAVIOR_FIELD: fixture.ALLOW,
                    "destination": "localSettings",
                },
            ],
        },
    ).encode()

    response = claude_hooks.ClaudeHookGateway().receive_hook(hook_request(payload))

    assert json.loads(response.reply) == {
        fixture.HOOK_SPECIFIC_OUTPUT: {
            "hookEventName": fixture.PERMISSION_REQUEST_HOOK,
            "decision": {fixture.BEHAVIOR_FIELD: fixture.ALLOW},
        },
    }


@pytest.mark.parametrize(
    "document",
    [
        {
            fixture.HOOK_EVENT_NAME_FIELD: fixture.PERMISSION_REQUEST_HOOK,
            fixture.TOOL_NAME_FIELD: fixture.BASH_TOOL,
        },
        {
            fixture.HOOK_EVENT_NAME_FIELD: "Notification",
            "notification_type": "permission_prompt",
        },
    ],
)
def test_claude_does_not_approve_non_chrome(
    document: dict[str, JsonValue],
) -> None:
    """Verify claude does not approve a non chrome or notification permission."""
    payload = json.dumps(
        {
            fixture.SESSION_ID_FIELD: fixture.CLAUDE_SESSION_ID,
            fixture.TRANSCRIPT_PATH: fixture.WORK_CLAUDE_JSONL_PATH,
            fixture.CWD_FIELD: fixture.WORK_PATH,
            **document,
        },
    ).encode()

    response = claude_hooks.ClaudeHookGateway().receive_hook(hook_request(payload))

    assert response.reply == b""
