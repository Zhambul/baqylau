# Copyright (c) 2026 Zhambyl Yermagambet
"""The record fields that Codex 0.156 added are accepted."""

import json
from pathlib import Path

import pytest

from harness.impl.codex.canonical import source_catalog
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import raw_event
from tests.plugin_tests.support_values import JsonValue

SESSION_ID = "01a0d91d-f968-7d10-a1ed-ee740a35628d"


def test_header_with_workspace_roots_is_a_lead(tmp_path: Path) -> None:
    """A session header that names its workspace roots still marks a lead rollout."""
    rollout = tmp_path / f"rollout-2026-09-25T23-10-15-{SESSION_ID}.jsonl"
    header = {
        "timestamp": "2026-09-25T15:10:15.527Z",
        "ordinal": 0,
        fixture.TYPE_FIELD: "session_meta",
        fixture.PAYLOAD_FIELD: {"id": SESSION_ID, "session_id": SESSION_ID, "runtime_workspace_roots": [str(tmp_path)]},
    }
    rollout.write_text(f"{json.dumps(header)}\n", encoding=fixture.TEXT_ENCODING)
    assert source_catalog.lead_rollout(str(rollout))


@pytest.mark.parametrize("record", [
    {
        fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
        fixture.PAYLOAD_FIELD: {
            fixture.TYPE_FIELD: fixture.TASK_STARTED_ID,
            fixture.TURN_ID_FIELD: fixture.TURN_ONE_ID,
            "root_turn_id": fixture.TURN_ONE_ID,
        },
    },
    {fixture.TYPE_FIELD: "turn_context", fixture.PAYLOAD_FIELD: {"disabled_plugin_ids": []}},
    {
        fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
        fixture.PAYLOAD_FIELD: {
            fixture.TYPE_FIELD: "thread_settings_applied",
            "thread_settings": {"runtime_workspace_roots": ["/work"], "disabled_plugin_ids": []},
        },
    },
    {
        fixture.TYPE_FIELD: "compacted",
        fixture.PAYLOAD_FIELD: {"message": "", "replacement_history_metadata": [{"client_authored": False}]},
    },
], ids=["task-started-root-turn", "turn-context-disabled-plugins", "thread-settings-new-fields", "compacted-metadata"])
def test_new_record_fields_translate(record: dict[str, JsonValue]) -> None:
    """A task start with its root turn and a turn context with disabled plugins translate."""
    CodexCanonicalTranslator().translate(
        raw_event(record, harness=CODEX_HARNESS, source_type=fixture.ROLLOUT_SOURCE, raw_event_id="codex-0156"),
    )
