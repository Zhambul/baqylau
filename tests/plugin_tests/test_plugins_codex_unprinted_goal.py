# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep a Codex goal that a script set but did not print."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from domain import event_work, work_state
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from tests.harness_names import CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event

if TYPE_CHECKING:
    from harness.models.raw_events import TranslationResult
    from tests.plugin_tests.support_values import JsonValue

FAILED_SCRIPT_TEXT = "Script failed\nError: the goal tool refused\n"


def _translate(
    translator: CodexCanonicalTranslator, payload: dict[str, JsonValue], raw_event_id: str,
) -> TranslationResult:
    return translator.translate(raw_event(
        {fixture.TYPE_FIELD: fixture.RESPONSE_ITEM, fixture.PAYLOAD_FIELD: payload},
        harness=CODEX_HARNESS, source_type=fixture.ROLLOUT_SOURCE, raw_event_id=raw_event_id,
    ))


def _script_goal(translator: CodexCanonicalTranslator, script: str, output: str) -> list[event_work.GoalChanged]:
    call_id = f"goal-call-{len(script)}"
    _translate(translator, {
        fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID, fixture.NAME_FIELD: fixture.EXEC,
        fixture.CALL_ID_FIELD: call_id, fixture.INPUT_FIELD: script,
    }, f"{call_id}-call")
    result = _translate(translator, {
        fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID, fixture.CALL_ID_FIELD: call_id,
        fixture.OUTPUT_FIELD: [{fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID, fixture.TEXT_FIELD: output}],
    }, f"{call_id}-result")
    return [event.payload for event in payloads(result, event_work.GoalChanged)]


def test_unprinted_goal_comes_from_the_call() -> None:
    """Make the goal fact from a completed call whose script printed something else."""
    translator = CodexCanonicalTranslator()
    created = _script_goal(
        translator,
        'const r = await tools.create_goal({objective:"Ship it"}); text("created");',
        f"{fixture.SCRIPT_COMPLETED_OUTPUT_TEXT}created",
    )
    completed = _script_goal(
        translator,
        'const r = await tools.update_goal({status:"complete"}); text("completed");',
        f"{fixture.SCRIPT_COMPLETED_OUTPUT_TEXT}completed",
    )
    goals = [*created, *completed]
    assert [goal.objective for goal in goals] == [fixture.SHIP_IT_TEXT, fixture.SHIP_IT_TEXT]
    assert [goal.state for goal in goals] == [work_state.GoalState.ACTIVE, work_state.GoalState.COMPLETED]


@pytest.mark.parametrize("script", ['const r = await tools.create_goal({objective:"Ship it"}); text("created");'])
def test_failed_script_sets_no_goal(script: str) -> None:
    """Make no goal fact when the script that called the goal tool failed."""
    assert _script_goal(CodexCanonicalTranslator(), script, FAILED_SCRIPT_TEXT) == []
