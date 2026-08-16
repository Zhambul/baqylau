"""An operation: a command, a search, a tool call — what it ran and how it went."""

from __future__ import annotations

import html
import json

from dashboard.render.items.item import (
    DashboardItem,
    ItemState,
    SummaryKind,
    block_html,
    content_reference,
    outcome_attribute,
    plain_text,
)
from dashboard.render.markdown import md_html
from domain.values import StructuredContent
from engine.projections import OperationActivity


def operation_text(activity: OperationActivity) -> str:
    return activity.output_text() or plain_text(activity.arguments)


def operation_summary_kind(activity: OperationActivity) -> SummaryKind:
    if activity.execution in ("background", "monitor"):
        return activity.execution
    if activity.category == "message":
        return "message_delivery"
    if activity.category is None:
        raise ValueError(f"operation {activity.operation_id} has no canonical category")
    return activity.category


def operation_html(activity: OperationActivity, text: str, state: str | None) -> str:
    title = activity.description or activity.native_name or activity.category or "operation"
    arguments = plain_text(activity.arguments)
    if activity.category == "skill":
        skill_name = arguments.strip() or str(title)
        note_text = f"Skill({skill_name})"
        if state in ("failed", "cancelled"):
            note_text += " failed"
        note_html = (
            f'<div class="anote" data-out="{outcome_attribute(state)}">'
            '<span class="anmark">⏺</span>'
            f'<span class="atext">{html.escape(note_text)}</span></div>'
        )
        body_text = text or arguments
        return block_html(
            header_html=note_html,
            summary="",
            body_html=f'<div class="md">{md_html(body_text)}</div>' if body_text else "",
            state=state,
            started_at=activity.context.started_at,
            finished_at=activity.context.finished_at,
            note=True,
        )
    argument_summary: str | None
    if isinstance(activity.arguments, StructuredContent):
        argument_summary = json.dumps(
            json.loads(activity.arguments.json_text),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    else:
        argument_summary = arguments.strip().splitlines()[0] if arguments.strip() else None
    summary = activity.description or argument_summary or str(title)
    body_html = f'<pre class="ogut">{html.escape(text)}</pre>' if text else ""
    quiet = activity.category == "shell" or activity.execution in ("background", "monitor")
    if quiet:
        kind = ""
        if activity.execution == "background":
            kind = '<span class="atext">background</span>'
        elif activity.execution == "monitor":
            kind = '<span class="atext">monitor</span>'
        header_html = f'<span class="anmark">⏺</span>{kind}'
    else:
        header_html = (
            '<div class="anote"><span class="anmark">⏺</span>'
            f'<span class="atext">{html.escape(str(title))}</span></div>'
        )
    return block_html(
        header_html=header_html,
        summary=summary,
        body_html=body_html,
        state=state,
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
        quiet=quiet,
    )


def present_operation(activity: OperationActivity) -> DashboardItem:
    actor_id = activity.context.actor_id
    text = operation_text(activity)
    state: ItemState | None = None
    if activity.state == "running":
        state = "running"
    elif activity.outcome in ("succeeded", "failed", "cancelled"):
        state = activity.outcome
    reference = None
    if activity.content_event_id is not None and activity.content_field is not None:
        reference = content_reference(
            activity.content_event_id,
            activity.content_field,
            text,
        )
    command_reference = (
        f"{activity.context.source_event_ids[0]}:operation_command"
        if activity.arguments is not None
        else None
    )
    output_reference = (
        f"{activity.content_event_id}:operation_output"
        if activity.content_event_id is not None
        and (activity.result is not None or activity.progress)
        else None
    )
    return DashboardItem(
        activity.context.activity_id,
        "operation",
        operation_summary_kind(activity),
        actor_id,
        state,
        operation_html(activity, text, state),
        text,
        reference,
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
        command_reference=command_reference,
        output_reference=output_reference,
    )
