"""The session organising itself: a task, a compaction, work handed to an actor."""

from __future__ import annotations

import html

from dashboard.render.items.item import (
    DashboardItem,
    block_html,
    content_reference,
    duration_text,
    outcome_attribute,
    plain_text,
)
from dashboard.render.markdown import md_html
from engine.projections import (
    ActorAssignmentActivity,
    CompactionActivity,
    EffortChangeActivity,
    ModelChangeActivity,
    TaskActivity,
)
from domain.values import ModelReference


def _model_label(model: ModelReference) -> str:
    return model.display_name or model.native_id


def present_task(activity: TaskActivity) -> DashboardItem:
    actor_id = activity.context.actor_id
    task = activity.change
    text = f"task #{task.label}"
    if task.subject:
        text += f" · {task.subject}"
    return DashboardItem(
        activity.context.activity_id,
        "task",
        "task",
        actor_id,
        "succeeded" if task.state == "completed" else None,
        (
            '<div class="anote"><span class="anmark">⏺</span>'
            f'<span class="atext">{html.escape(text)}</span></div>'
        ),
        text,
        None,
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
    )


def present_compaction(activity: CompactionActivity) -> DashboardItem:
    actor_id = activity.context.actor_id
    text = "compacted"
    return DashboardItem(
        activity.context.activity_id,
        "compaction",
        "compaction",
        actor_id,
        "succeeded",
        (
            '<div class="anote"><span class="anmark">⏺</span>'
            '<span class="atext">compacted</span></div>'
        ),
        text,
        None,
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
    )


def present_model_change(activity: ModelChangeActivity) -> DashboardItem:
    actor_id = activity.context.actor_id
    previous_label = _model_label(activity.previous)
    current_label = _model_label(activity.current)
    text = f"model {previous_label} → {current_label}"
    return DashboardItem(
        activity.context.activity_id,
        "model_changed",
        "model_changed",
        actor_id,
        "succeeded",
        (
            '<div class="anote" data-out="ok"><span class="anmark">⏺</span>'
            f'<span class="atext">model <strong>{html.escape(previous_label)}</strong>'
            f' → <strong>{html.escape(current_label)}</strong></span></div>'
        ),
        text,
        None,
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
    )


def present_effort_change(activity: EffortChangeActivity) -> DashboardItem:
    actor_id = activity.context.actor_id
    text = f"effort {activity.previous} → {activity.current}"
    return DashboardItem(
        activity.context.activity_id,
        "effort_changed",
        "effort_changed",
        actor_id,
        "succeeded",
        (
            '<div class="anote" data-out="ok"><span class="anmark">⏺</span>'
            f'<span class="atext">effort <strong>{html.escape(activity.previous)}</strong>'
            f' → <strong>{html.escape(activity.current)}</strong></span></div>'
        ),
        text,
        None,
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
    )


def present_assignment(activity: ActorAssignmentActivity) -> DashboardItem:
    actor_id = activity.context.actor_id
    launch_detail = activity.prompt if activity.prompt is not None else activity.brief
    content = activity.result if activity.result is not None else launch_detail
    text = activity.reason or plain_text(content)
    state = "running" if activity.state == "running" else activity.outcome
    item_state = state if state in ("running", "succeeded", "failed", "cancelled") else None
    phase = "started" if activity.state == "running" else "finished"
    duration = ""
    if activity.context.started_at is not None and activity.context.finished_at is not None:
        duration = f" · {duration_text(activity.context.finished_at - activity.context.started_at)}"
    assignment_name = (
        plain_text(activity.brief).strip()
        if activity.brief is not None
        else str(activity.assignment_id)
    )
    note_text = f'Agent "{assignment_name}" {phase}{duration}'
    note_html = (
        f'<div class="anote" data-out="{outcome_attribute(item_state)}">'
        '<span class="anmark">⏺</span>'
        f'<span class="atext">{html.escape(note_text)}</span></div>'
    )
    launch_field = "prompt" if activity.prompt is not None else "brief"
    reference = (
        content_reference(activity.context.source_event_ids[-1], "result", text)
        if activity.result is not None
        else content_reference(activity.context.source_event_ids[0], launch_field, text)
        if activity.reason is None
        else None
    )
    body_html = f'<div class="md">{md_html(text)}</div>'
    if (
        activity.assigned_actor_name
        and activity.result is None
        and activity.reason is None
    ):
        body_html = (
            '<div class="md"><p><strong>agent:</strong> '
            f"{html.escape(activity.assigned_actor_name)}</p></div>{body_html}"
        )
    return DashboardItem(
        activity.context.activity_id,
        "actor_assignment",
        "actor_assignment",
        actor_id,
        item_state,
        block_html(
            header_html=note_html,
            summary="",
            body_html=body_html,
            state=item_state,
            started_at=activity.context.started_at,
            finished_at=activity.context.finished_at,
            note=True,
        ),
        text,
        reference,
        turn_id=str(activity.context.turn_id) if activity.context.turn_id else None,
        actor_assignment_id=str(activity.assignment_id),
        actor_assignment_phase="started" if activity.state == "running" else "finished",
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
    )
