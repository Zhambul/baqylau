# Copyright (c) 2026 Zhambyl Yermagambet
"""Codex file and opaque execution translation tests."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from domain.event_resource import FileAccessed
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event
from tests.plugin_tests.support_values import text_of


def test_codex_current_file_change_emits_shared() -> None:
    """Verify codex current file change emits the shared file facts."""
    translated = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.ITEM_COMPLETED,
                    fixture.ITEM_FIELD: {
                        fixture.TYPE_FIELD: "FileChange",
                        fixture.ID_FIELD: "edit-one",
                        fixture.STATUS_FIELD: fixture.COMPLETED,
                        "changes": {
                            fixture.WORK_A_PY_PATH: {
                                fixture.TYPE_FIELD: "update",
                                "unified_diff": "@@ -1 +1 @@\n-old\n+new\n",
                                "move_path": None,
                            },
                            "/work/b.py": {
                                fixture.TYPE_FIELD: "add",
                                fixture.CONTENT_FIELD: "print('captured')\n",
                            },
                        },
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="file-change",
        ),
    )

    files = [event.payload for event in payloads(translated, FileAccessed)]
    assert [(file.path, file.action) for file in files] == [
        (fixture.WORK_A_PY_PATH, "updated"),
        ("/work/b.py", "created"),
    ]
    assert files[0].unified_diff == "@@ -1 +1 @@\n-old\n+new\n"
    assert text_of(files[1].content) == "print('captured')\n"
    # The patch itself is not a command and has no life of its own: the files it
    # touched are the whole fact.
    assert len(translated.canonical_events) == len(files)
    assert {file.outcome for file in files} == {fixture.SUCCEEDED}


def test_codex_opaque_exec_output_does_not_create() -> None:
    """Verify codex opaque exec output does not create a finish without a start."""
    translator = CodexCanonicalTranslator()
    started = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "opaque-one",
                    fixture.INPUT_FIELD: "const hits = ALL_TOOLS.filter(x => x.name); text(hits);",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="opaque-call",
            source_position=fixture.FORTY_TEXT,
        ),
    )
    finished = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "opaque-one",
                    fixture.OUTPUT_FIELD: "Script completed\nOutput:\n[]",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="opaque-output",
            source_position=fixture.FORTY_ONE_TEXT,
        ),
    )

    assert started.canonical_events == ()
    assert finished.canonical_events == ()


@pytest.mark.parametrize(
    ("call_input", "expected_facts"),
    [
        # No `tools.<fn>(…)` at all: the output belongs to no call this
        # vocabulary has a fact for, and inventing one is worse than none.
        ("const hits = ALL_TOOLS.filter(x => x.name); text(hits);", 0),
        # A file read, whose PATH is in the call the scan recovered — without
        # it the result would be a fact about no file.
        ('text(await tools.view_image({path:"/test-data/image.png"}));', 1),
    ],
)
def test_codex_output_recovers_its_call_pairing(
    tmp_path: Path,
    call_input: str,
    expected_facts: int,
) -> None:
    """Verify codex output recovers its call pairing across a restart."""
    call = {
        fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
        fixture.PAYLOAD_FIELD: {
            fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
            fixture.NAME_FIELD: fixture.EXEC,
            fixture.CALL_ID_FIELD: "restart-one",
            fixture.INPUT_FIELD: call_input,
        },
    }
    call_line = f"{json.dumps(call)}\n"
    rollout_path = tmp_path / fixture.ROLLOUT_JSONL_PATH
    rollout_path.write_text(call_line)
    output = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "restart-one",
                    fixture.OUTPUT_FIELD: "Script completed\nOutput:\nresult",
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="restart-output",
            source_position=str(len(call_line.encode())),
        ),
        source_name=str(rollout_path),
    )

    translated = CodexCanonicalTranslator().translate(output)

    assert len(translated.canonical_events) == expected_facts
    if expected_facts:
        assert payloads(translated, FileAccessed)[0].payload.path == "/test-data/image.png"


def test_codex_image_tool_output_accepts_text(tmp_path: Path) -> None:
    """Verify codex image tool output accepts text and image content parts."""
    call = {
        fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
        fixture.PAYLOAD_FIELD: {
            fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
            fixture.NAME_FIELD: fixture.EXEC,
            fixture.CALL_ID_FIELD: "image-one",
            fixture.INPUT_FIELD: 'text(await tools.view_image({path:"/test-data/marker.png"}));',
        },
    }
    call_line = f"{json.dumps(call)}\n"
    rollout_path = tmp_path / "image-rollout.jsonl"
    rollout_path.write_text(call_line)
    output = replace(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "image-one",
                    fixture.OUTPUT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                            fixture.TEXT_FIELD: fixture.SCRIPT_COMPLETED_OUTPUT_TEXT,
                        },
                        {
                            fixture.TYPE_FIELD: "input_image",
                            "image_url": "data:image/png;base64,AA==",
                            "detail": fixture.HIGH,
                        },
                    ],
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="image-output",
            source_position=str(len(call_line.encode())),
        ),
        source_name=str(rollout_path),
    )

    translated = CodexCanonicalTranslator().translate(output)

    assert translated.decision == fixture.TRANSLATED
    assert payloads(translated, FileAccessed)[0].payload.path == "/test-data/marker.png"
