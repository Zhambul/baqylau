# Copyright (c) 2026 Zhambyl Yermagambet
"""Recognize complete native interrupt markers, not quoted user text."""

import json

import pytest

from harness.impl.claude_code.canonical.transcript_model_activity import ResultsTranscriptRecord
from harness.impl.claude_code.canonical.transcript_model_core import PromptTranscriptRecord
from harness.impl.claude_code.canonical.transcript_parser import parse_line


@pytest.mark.parametrize("blocks", [False, True])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[Request interrupted by user]", True),
        ("[Request interrupted by user for tool use]", True),
        ("Explain [Request interrupted by user]", False),
        ("[Request interrupted by user] then continue", False),
    ],
)
def test_interrupt_marker_without_id(text: str, *, blocks: bool, expected: bool) -> None:
    """Use exact markers in both native text formats."""
    content = [{"type": "text", "text": text}] if blocks else text
    record = parse_line(json.dumps(
        {"type": "user", "message": {"role": "user", "content": content}},
    ))
    assert isinstance(record, (PromptTranscriptRecord, ResultsTranscriptRecord))
    assert record.interrupted is expected
    assert record.meta is expected
