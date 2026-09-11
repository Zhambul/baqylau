# Copyright (c) 2026 Zhambyl Yermagambet
"""Build Claude Code completion facts."""

from harness.impl.claude_code.canonical import (
    tool_attention,
    tool_completion_dependencies as dependencies,
    tool_kind_values as kind_values,
    tool_resource_facts,
)
from harness.impl.claude_code.canonical.tool_file_facts import file_facts
from harness.impl.claude_code.canonical.tool_result_models import FinishedToolIdentity, FinishedToolResult
from harness.impl.claude_code.canonical.tool_values import FINISHED_PHASE
from harness.models import raw_events


def finished_tool_facts(
    raw_event: raw_events.RawEvent,
    finished_tool_identity: FinishedToolIdentity,
    finished_tool_result: FinishedToolResult,
) -> list[dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]]:
    """Select the completion mapper for a tool result.

    Returns:
        The canonical completion events for the tool kind.

    """
    if finished_tool_identity.kind == kind_values.ToolKind.SKILL:
        return skill_finished_fact(raw_event, finished_tool_identity, finished_tool_result)
    if finished_tool_identity.kind == kind_values.ToolKind.QUESTION:
        return [question_answered_fact(raw_event, finished_tool_identity, finished_tool_result)]
    if finished_tool_identity.kind == kind_values.ToolKind.PLAN:
        return [plan_resolved_fact(raw_event, finished_tool_identity, finished_tool_result)]
    if finished_tool_identity.kind == kind_values.ToolKind.FILE:
        return file_facts(raw_event, finished_tool_identity, finished_tool_result)
    return resource_finished_facts(raw_event, finished_tool_identity, finished_tool_result)


def resource_finished_facts(
    raw_event: raw_events.RawEvent,
    finished_tool_identity: FinishedToolIdentity,
    finished_tool_result: FinishedToolResult,
) -> list[dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]]:
    """Map a completed search, web, browser, or worktree operation.

    Returns:
        The resource completion event in a list.

    """
    if finished_tool_identity.kind == kind_values.ToolKind.SEARCH:
        return [tool_resource_facts.search_finished_fact(raw_event, finished_tool_identity, finished_tool_result)]
    if finished_tool_identity.kind == kind_values.ToolKind.WEB:
        return [tool_resource_facts.web_finished_fact(raw_event, finished_tool_identity, finished_tool_result)]
    if finished_tool_identity.kind == kind_values.ToolKind.BROWSER:
        return [tool_resource_facts.browser_finished_fact(raw_event, finished_tool_identity, finished_tool_result)]
    return [tool_resource_facts.worktree_finished_fact(raw_event, finished_tool_identity, finished_tool_result)]


def skill_finished_fact(
    raw_event: raw_events.RawEvent,
    finished_tool_identity: FinishedToolIdentity,
    finished_tool_result: FinishedToolResult,
) -> list[dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]]:
    """Map a skill result after its initial launch response.

    Returns:
        The skill completion event, or no events for a successful launch placeholder.

    """
    skill_answer = dependencies.content.content_text(finished_tool_result.answer).strip()
    if finished_tool_result.outcome == dependencies.outcomes.Outcome.SUCCEEDED and (
        skill_answer in {"", "{}"} or skill_answer.startswith("Launching skill:")
    ):
        return []
    skill_id = dependencies.ids.skill_id_from_claude_code_call(finished_tool_identity.call_id)
    payload = dependencies.event_resource.SkillFinished(
        skill_id, finished_tool_result.outcome, finished_tool_result.answer,
    )
    draft = dependencies.support.CanonicalEventDraft(
        kind_values.ToolKind.SKILL.value,
        str(skill_id),
        FINISHED_PHASE,
        payload,
    )
    return [dependencies.support.event(raw_event, draft)]


def question_answered_fact(
    raw_event: raw_events.RawEvent,
    finished_tool_identity: FinishedToolIdentity,
    finished_tool_result: FinishedToolResult,
) -> dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]:
    """Build a question answer from the recorded tool arguments.

    Returns:
        The answer event for the tool call's attention identifier.

    """
    attention_id = dependencies.ids.attention_id_from_claude_code_call(finished_tool_identity.call_id)
    payload = dependencies.event_work.QuestionAnswered(
        attention_id,
        tool_attention.attention_answers(
            finished_tool_identity.arguments,
            finished_tool_result.response.answers,
        ),
        None,
    )
    draft = dependencies.support.CanonicalEventDraft(
        kind_values.ToolKind.QUESTION.value,
        str(attention_id),
        "answered",
        payload,
    )
    return dependencies.support.event(raw_event, draft)


def plan_resolved_fact(
    raw_event: raw_events.RawEvent,
    finished_tool_identity: FinishedToolIdentity,
    finished_tool_result: FinishedToolResult,
) -> dependencies.event_base.CanonicalEvent[dependencies.event_base.EventPayload]:
    """Map the native plan response to a canonical resolution.

    Returns:
        The plan resolution event.

    """
    attention_id = dependencies.ids.attention_id_from_claude_code_call(finished_tool_identity.call_id)
    resolution = tool_attention.plan_resolution(
        finished_tool_result.native_response, failed=finished_tool_result.failed,
    )
    payload = dependencies.event_work.PlanResolved(attention_id, *resolution)
    draft = dependencies.support.CanonicalEventDraft(
        kind_values.ToolKind.PLAN.value,
        str(attention_id),
        dependencies.raw_event_builders.plan_resolution_phase(payload),
        payload,
    )
    return dependencies.support.event(raw_event, draft)
