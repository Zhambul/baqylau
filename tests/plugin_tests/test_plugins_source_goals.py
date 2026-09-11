# Copyright (c) 2026 Zhambyl Yermagambet
"""Shared goal source tests."""

from __future__ import annotations

import pytest

from domain import (
    event_shell,
    event_work,
    ids as domain_ids,
    work_state,
)
from harness.impl.claude_code.canonical.translator import ClaudeCanonicalTranslator
from harness.impl.codex.canonical.translator import CodexCanonicalTranslator
from harness.models import raw_events as raw_event_models
from tests.harness_names import CLAUDE_CODE_HARNESS, CODEX_HARNESS
from tests.plugin_tests import vocabulary as fixture
from tests.plugin_tests.support_events import payloads, raw_event


def test_claude_goal_status_is_canon_goal_state() -> None:
    """Verify claude goal status is canonical goal state."""
    active = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ATTACHMENT,
                fixture.UUID_FIELD: "goal-active",
                fixture.ATTACHMENT: {
                    fixture.TYPE_FIELD: "goal_status",
                    "met": False,
                    "condition": fixture.SUCCESS_MESSAGE,
                    fixture.REASON_FIELD: "One test remains",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="goal-active",
        ),
    )
    completed = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.ATTACHMENT,
                fixture.UUID_FIELD: "goal-completed",
                fixture.ATTACHMENT: {
                    fixture.TYPE_FIELD: "goal_status",
                    "met": True,
                    "condition": fixture.SUCCESS_MESSAGE,
                    fixture.REASON_FIELD: "The suite is green",
                },
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="goal-completed",
        ),
    )

    assert payloads(active, event_work.GoalChanged)[0].payload == event_work.GoalChanged(
        fixture.SUCCESS_MESSAGE,
        work_state.GoalState.ACTIVE,
        "One test remains",
    )
    assert payloads(completed, event_work.GoalChanged)[0].payload == event_work.GoalChanged(
        fixture.SUCCESS_MESSAGE,
        work_state.GoalState.COMPLETED,
        "The suite is green",
    )

    cleared = ClaudeCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.SYSTEM,
                fixture.UUID_FIELD: "goal-cleared",
                fixture.CONTENT_FIELD: "Goal cleared: All tests pass",
            },
            harness=CLAUDE_CODE_HARNESS,
            source_type=fixture.TRANSCRIPT_SOURCE,
            raw_event_id="goal-cleared",
        ),
    )
    assert payloads(cleared, event_work.GoalChanged)[0].payload == event_work.GoalChanged(
        fixture.SUCCESS_MESSAGE, work_state.GoalState.CLEARED, None,
    )


def test_codex_goal_and_plan_use_shared_goal() -> None:
    """Verify codex goal and plan use shared goal and task events."""
    translator = CodexCanonicalTranslator()
    goal = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: "thread_goal_updated",
                    "goal": {
                        "objective": fixture.SHIP_IT_TEXT,
                        fixture.STATUS_FIELD: "active",
                        "tokensUsed": 20,
                        "timeUsedSeconds": 3,
                        "createdAt": 1787805991,
                        "updatedAt": 1787806113,
                    },
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="goal-one",
        ),
    )
    plan = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: fixture.PLAN_ONE,
                    fixture.INPUT_FIELD: (
                        "const result = await tools.update_plan({plan:["
                        '{step:"Inspect",status:"completed"},'
                        '{step:"Implement",status:"in_progress"}]}); text(result);'
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id=fixture.PLAN_ONE,
        ),
    )

    assert payloads(goal, event_work.GoalChanged)[0].payload == event_work.GoalChanged(
        fixture.SHIP_IT_TEXT, work_state.GoalState.ACTIVE, None,
    )
    assert payloads(plan, event_work.TaskListChanged)[0].payload.task_ids == (
        domain_ids.TaskId("session-one:lead:plan:1"),
        domain_ids.TaskId("session-one:lead:plan:2"),
    )
    task_changes = payloads(plan, event_work.TaskChanged)
    assert [event.payload.subject for event in task_changes] == ["Inspect", "Implement"]
    assert [event.payload.state for event in task_changes] == [
        fixture.COMPLETED,
        "in_progress",
    ]
    assert not payloads(plan, event_shell.ShellStarted)


def test_codex_goal_tool_output_updates_shared() -> None:
    """Verify codex goal tool output updates the shared goal."""
    translation = CodexCanonicalTranslator().translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.ID_FIELD: "goal-result-one",
                    fixture.CALL_ID_FIELD: "goal-call-one",
                    fixture.OUTPUT_FIELD: [
                        {
                            fixture.TYPE_FIELD: fixture.INPUT_TEXT_ID,
                            fixture.TEXT_FIELD: (
                                '{"goal":{"threadId":"session-one",'
                                '"objective":"Ship it","status":"complete",'
                                '"tokensUsed":20,"timeUsedSeconds":3},'
                                '"remainingTokens":null}'
                            ),
                        },
                    ],
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="goal-result-one",
        ),
    )

    assert payloads(translation, event_work.GoalChanged)[0].payload == event_work.GoalChanged(
        fixture.SHIP_IT_TEXT,
        work_state.GoalState.COMPLETED,
        None,
    )


def test_codex_state_tool_batch_keeps_all_goal() -> None:
    """Verify codex state tool batch keeps all goal and task changes."""
    translator = CodexCanonicalTranslator()
    batch = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_ID,
                    fixture.NAME_FIELD: fixture.EXEC,
                    fixture.CALL_ID_FIELD: "state-batch-one",
                    fixture.INPUT_FIELD: (
                        'const a = await tools.create_goal({objective:"Ship it"});'
                        "const b = await tools.update_plan({plan:["
                        '{step:"Inspect",status:"completed"},'
                        '{step:"Finish",status:"completed"}]});'
                        'const c = await tools.update_goal({status:"complete"});'
                        'text("");'
                    ),
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="state-batch-one",
        ),
    )
    output = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.RESPONSE_ITEM,
                fixture.PAYLOAD_FIELD: {
                    fixture.TYPE_FIELD: fixture.CUSTOM_TOOL_CALL_OUTPUT_ID,
                    fixture.CALL_ID_FIELD: "state-batch-one",
                    fixture.OUTPUT_FIELD: fixture.SCRIPT_COMPLETED_OUTPUT_TEXT,
                },
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="state-batch-result",
        ),
    )

    assert [event.payload.state for event in payloads(batch, event_work.GoalChanged)] == [
        "active",
        fixture.COMPLETED,
    ]
    assert [event.payload.subject for event in payloads(batch, event_work.TaskChanged)] == [
        "Inspect",
        "Finish",
    ]
    assert payloads(output, event_shell.ShellFinished) == []


def test_codex_goal_state_is_strict_and_clear() -> None:
    """Verify codex goal state is strict and clear removes the goal."""
    translator = CodexCanonicalTranslator()
    cleared = translator.translate(
        raw_event(
            {
                fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                fixture.PAYLOAD_FIELD: {fixture.TYPE_FIELD: "thread_goal_cleared"},
            },
            harness=CODEX_HARNESS,
            source_type=fixture.ROLLOUT_SOURCE,
            raw_event_id="goal-cleared",
        ),
    )
    assert payloads(cleared, event_work.GoalChanged)[0].payload == event_work.GoalChanged(
        None, work_state.GoalState.CLEARED, None,
    )

    with pytest.raises(raw_event_models.TranslationError, match="unknown Codex goal state"):
        translator.translate(
            raw_event(
                {
                    fixture.TYPE_FIELD: fixture.EVENT_MSG_ID,
                    fixture.PAYLOAD_FIELD: {
                        fixture.TYPE_FIELD: "thread_goal_updated",
                        "goal": {"objective": fixture.SHIP_IT_TEXT, fixture.STATUS_FIELD: "mystery"},
                    },
                },
                harness=CODEX_HARNESS,
                source_type=fixture.ROLLOUT_SOURCE,
                raw_event_id="goal-invalid",
            ),
        )
