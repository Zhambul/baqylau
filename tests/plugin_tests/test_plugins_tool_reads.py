# Copyright (c) 2026 Zhambyl Yermagambet
"""Claude file tool tests."""

from __future__ import annotations

import pytest

from domain.content import TextContent
from domain.event_resource import (
    FileAccessed,
    SearchPerformed,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from tests.harness_names import CLAUDE_CODE_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import JsonValue

type CodexTranslationDecisionCase = tuple[dict[str, JsonValue], str]


def test_claude_read_accepts_native_token_cap() -> None:
    """Verify claude read accepts the native token cap marker."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "read-truncated",
                fixture.TOOL_NAME_FIELD: fixture.READ_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.FILE_PATH_FIELD: fixture.WORK_LARGE_TXT_PATH},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="read-truncated-call",
        ),
    )

    translated = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "read-truncated",
                fixture.TOOL_NAME_FIELD: fixture.READ_TOOL,
                fixture.TOOL_INPUT_FIELD: {fixture.FILE_PATH_FIELD: fixture.WORK_LARGE_TXT_PATH},
                fixture.TOOL_RESPONSE_FIELD: {
                    fixture.TYPE_FIELD: fixture.TEXT_FIELD,
                    "file": {
                        "filePath": fixture.WORK_LARGE_TXT_PATH,
                        fixture.CONTENT_FIELD: "visible prefix",
                        "numLines": 1,
                        "startLine": 1,
                        "totalLines": 50000,
                        "truncatedByTokenCap": True,
                    },
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="read-truncated-result",
        ),
    )

    accessed = payloads(translated, FileAccessed)[0].payload
    assert accessed.path == fixture.WORK_LARGE_TXT_PATH
    assert accessed.content == TextContent("visible prefix")


def test_claude_read_accepts_native_image_sidecar() -> None:
    """Verify claude read accepts the native image sidecar."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ASSISTANT,
                fixture.MESSAGE_FIELD: {
                    fixture.CONTENT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.TOOL_USE_ID,
                            fixture.ID_FIELD: "read-image",
                            fixture.NAME_FIELD: fixture.READ_TOOL,
                            fixture.INPUT_FIELD: {fixture.FILE_PATH_FIELD: "/work/image.png"},
                        },
                    ],
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="read-image-call",
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
                            fixture.TOOL_USE_ID_FIELD: "read-image",
                            fixture.CONTENT_FIELD: [
                                {
                                    fixture.TYPE_FIELD: fixture.IMAGE,
                                    fixture.SOURCE: {
                                        fixture.TYPE_FIELD: fixture.BASE64,
                                        fixture.MEDIA_TYPE_FIELD: fixture.PNG_MEDIA_TYPE,
                                        fixture.DATA_FIELD: "aW1hZ2U=",
                                    },
                                },
                            ],
                        },
                    ],
                },
                fixture.TOOL_USE_RESULT: {
                    fixture.TYPE_FIELD: fixture.IMAGE,
                    "file": {
                        "filePath": "/work/image.png",
                        fixture.BASE64: "aW1hZ2U=",
                        fixture.TYPE_FIELD: fixture.PNG_MEDIA_TYPE,
                        "originalSize": 5,
                        "dimensions": {
                            "originalWidth": 10,
                            "originalHeight": 20,
                            "displayWidth": 10,
                            "displayHeight": 20,
                        },
                    },
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="read-image-result",
        ),
    )

    accessed = payloads(translated, FileAccessed)[0].payload
    assert accessed.path == "/work/image.png"
    assert accessed.outcome == fixture.SUCCEEDED


@pytest.mark.parametrize("native_name", ["Grep", "Glob"])
def test_claude_filename_search_hook_keeps(native_name: str) -> None:
    """Verify claude filename search hook keeps readable results."""
    translator = ClaudeCanonicalTranslator()
    translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "filename-search",
                fixture.TOOL_NAME_FIELD: native_name,
                fixture.TOOL_INPUT_FIELD: {"pattern": "*.py"},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id=f"{native_name}-call",
        ),
    )

    translated = translator.translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.POST_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "filename-search",
                fixture.TOOL_NAME_FIELD: native_name,
                fixture.TOOL_INPUT_FIELD: {"pattern": "*.py"},
                fixture.TOOL_RESPONSE_FIELD: {
                    "filenames": ["api/app.py", "tests/test_app.py"],
                    "numFiles": 2,
                    "truncated": False,
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id=f"{native_name}-result",
        ),
    )

    performed = payloads(translated, SearchPerformed)[0].payload
    assert performed.tool == native_name
    assert performed.result == TextContent("api/app.py\ntests/test_app.py")


def test_claude_bg_result_reader_is_known() -> None:
    """Verify claude background result reader is known duplicate plumbing."""
    translation = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.HOOK_EVENT_NAME_FIELD: fixture.PRE_TOOL_USE_HOOK,
                fixture.TOOL_USE_ID_FIELD: "task-output",
                fixture.TOOL_NAME_FIELD: "TaskOutput",
                fixture.TOOL_INPUT_FIELD: {fixture.TASK_ID: "native-task", "block": True},
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.HOOK_SOURCE,
            raw_event_id="task-output-call",
        ),
    )

    assert translation.canonical_events == ()
    assert translation.decision == fixture.IGNORED_NONSEMANTIC
