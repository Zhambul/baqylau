# Copyright (c) 2026 Zhambyl Yermagambet
"""Make no reasoning fact from OpenCode2 reasoning text that is only white space."""

from pathlib import Path

from harness.impl.opencode2 import conversation
from harness.impl.opencode2.records import NativeRecord

REASONING_RECORD = Path(__file__).parents[1] / "e2e/fixtures/audit_opencode2_reasoning.jsonl"


def test_blank_reasoning_makes_no_fact() -> None:
    """Make no reasoning fact from reasoning text that is only white space.

    The feed refuses an empty reasoning entry, as it does for Claude Code thinking.
    """
    record = NativeRecord.model_validate_json(REASONING_RECORD.read_text())
    details = record.event.details.model_copy(update={"text": "\n\n"})
    event = record.event.model_copy(update={"details": details})
    assert conversation.payloads(record)
    assert conversation.payloads(record.model_copy(update={"event": event})) == ()
