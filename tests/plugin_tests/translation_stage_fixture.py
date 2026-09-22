# Copyright (c) 2026 Zhambyl Yermagambet
"""Build native inputs for the harness lifecycle protocol tests."""

import json
from dataclasses import replace
from pathlib import Path

from domain import ids
from harness.impl.codex.canonical.record_session_meta import CodexHookPayload
from harness.models.raw_events import RawEvent
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import source_common_support
from tests.plugin_tests.support_events import raw_event
from tests.plugin_tests.support_values import JsonValue


def claude_prompt(text: str, *, directory: str = "/work") -> RawEvent:
    """Build one input which starts both a session and a turn.

    Returns:
        A native transcript record with a fixed logical identity.

    """
    return raw_event({
        "type": "user", "uuid": "prompt-one", "cwd": directory,
        "message": {"role": "user", "content": text},
    }, harness=CLAUDE_CODE_HARNESS, source_type="transcript", raw_event_id="prompt", source_position="0")


def claude_hook(name: str) -> RawEvent:
    """Build a native hook with the same identity as the prompt.

    Returns:
        A hook whose turn can be read through the public translator.

    """
    return raw_event({"hook_event_name": name}, harness=CLAUDE_CODE_HARNESS, source_type="hook", raw_event_id=name)


def codex_record(document: JsonValue, *, position: str = "10") -> RawEvent:
    """Build one native rollout record.

    Returns:
        The recorded JSON bytes with stable session and actor metadata.

    """
    return raw_event(document, harness=CODEX_HARNESS, source_type="rollout",
                     raw_event_id=f"codex-{position}", source_position=position)


def codex_boundary() -> RawEvent:
    """Build the native record which consumes a pending compaction.

    Returns:
        A compaction result with no token counts in the record itself.

    """
    return codex_record({"type": "compacted", "payload": {"message": "summary", "replacement_history": []}})


def codex_compaction(tmp_path: Path, *, before: int = 41) -> RawEvent:
    """Build a hook which can also confirm a missed session start.

    Returns:
        The hook and its private source file, without a live harness.

    """
    path = tmp_path / "rollout-2026-09-15T10-00-00-session-one.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {
        "id": "session-one", "cwd": "/work", "thread_source": "user",
    }}) + "\n", encoding="utf-8")
    hook = source_common_support.codex_hook_event(path, "PreCompact", "compact")
    document = CodexHookPayload.model_validate_json(hook.payload).model_copy(update={"before_tokens": before})
    return replace(hook, payload=document.model_dump_json().encode())


def core_input(document: JsonValue, source_type: str, *, name: str = "core") -> RawEvent:
    """Build a core input in a fixed session run.

    Returns:
        An input which uses only private test metadata.

    """
    return replace(raw_event(document, harness=CODEX_HARNESS, source_type=source_type, raw_event_id="core"),
                   source_name=name, terminal_window_id=ids.WindowId("test-window"))
