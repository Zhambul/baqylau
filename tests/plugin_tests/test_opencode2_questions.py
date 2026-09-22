# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the native question lifecycle against its recorded joins."""

from pathlib import Path

from domain import event_work
from harness.impl.opencode2 import questions
from harness.impl.opencode2.records import NativeRecord
from harness.impl.opencode2.tool_records import NativeTool, NativeToolError

FIXTURE = Path(__file__).parents[1] / "e2e/fixtures/audit_opencode2_cancelled_question.jsonl"


def test_aborted_question_resolves_without_input() -> None:
    """Verify a failed question resolves from its name and identity.

    The plugin joins the name and the input separately. A reload can lose the
    input join alone, and the failed call must still answer the question it
    named: the finish leg never reads the input.
    """
    called = NativeRecord.model_validate_json(FIXTURE.read_text().splitlines()[0])
    failed = called.model_copy(update={
        "event": called.event.model_copy(update={
            "type": "session.tool.failed",
            "details": called.event.details.model_copy(update={
                "error": NativeToolError(type="aborted", message="Tool execution interrupted"),
            }),
        }),
        "tool": NativeTool(name="question"),
    })
    payloads = questions.payloads(failed)
    assert len(payloads) == 1
    answered = payloads[0]
    assert isinstance(answered, event_work.QuestionAnswered)
    assert str(answered.attention_id) == str(called.event.details.id)
    assert answered.answers == ()
    assert answered.feedback == "Tool execution interrupted"
