# Copyright (c) 2026 Zhambyl Yermagambet
"""Check native permission identity and recorded decisions."""

from pathlib import Path

import pytest

from domain import event_work
from harness.impl.opencode2 import permissions
from harness.impl.opencode2.records import NativeRecord

FIXTURE = Path(__file__).parents[1] / "e2e/fixtures/audit_opencode2_permission.jsonl"


def test_permission_keeps_resource_and_identity() -> None:
    """Show the native resource and all available decisions."""
    record = NativeRecord.model_validate_json(FIXTURE.read_text())
    asked = permissions.payloads(record)[0]
    assert isinstance(asked, event_work.QuestionAsked)
    prompt = asked.questions[0]
    assert str(prompt.prompt_id) == str(asked.attention_id) == record.event.details.id
    assert record.event.details.resources[0] in prompt.prompt
    assert [choice.label for choice in prompt.choices] == ["Allow once", "Always allow", "Reject"]
    assert prompt.multiple is False


@pytest.mark.parametrize(("reply", "label"), [
    ("once", "Allow once"), ("always", "Always allow"), ("reject", "Reject"),
])
def test_permission_reply_resolves_same_request(reply: str, label: str) -> None:
    """Use the request identity and the native decision label."""
    path = FIXTURE.with_name("audit_opencode2_permission_replies.jsonl")
    records = (NativeRecord.model_validate_json(line) for line in path.read_text().splitlines())
    record = next(record for record in records if record.event.details.reply == reply)
    details = record.event.details
    answered = permissions.payloads(record)[0]
    assert isinstance(answered, event_work.QuestionAnswered)
    assert answered.attention_id == details.request_id
    assert str(answered.answers[0].prompt_id) == str(details.request_id)
    assert answered.answers[0].labels == (label,)
