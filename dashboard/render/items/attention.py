"""A question the session asked you, and the answer it got back."""

from __future__ import annotations

import html

from dashboard.render.items.item import (
    DashboardItem,
    content_reference,
)
from dashboard.render.markdown import md_html
from engine.projections import AttentionActivity


def attention_text(activity: AttentionActivity) -> str:
    if activity.phase == "resolved":
        if activity.feedback:
            return activity.feedback
        return "\n".join(value for answer in activity.answers for value in answer.values)
    blocks = []
    for prompt in activity.prompts:
        lines = [prompt.prompt] if prompt.prompt else []
        lines.extend(f"- {choice.label}" for choice in prompt.choices if choice.label)
        if lines:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def answer_html(activity: AttentionActivity) -> str | None:
    answers = {answer.prompt_id: answer.values for answer in activity.answers}
    rows = []
    for prompt in activity.prompts:
        values = answers.get(prompt.prompt_id)
        if values is None:
            continue
        heading = ""
        if prompt.title:
            heading += f'<span class="anshdr">{html.escape(prompt.title)}</span>'
        if prompt.prompt:
            heading += f'<span class="ansqt">{html.escape(prompt.prompt)}</span>'
        value_markup = "".join(
            f'<span class="ansv">{html.escape(value)}</span>' for value in values
        ) or '<span class="ansv none">—</span>'
        rows.append(
            f'<div class="ansq"><div class="ansqh">{heading}</div>'
            f'<div class="ansvs">{value_markup}</div></div>'
        )
    return f'<div class="ansqa">{"".join(rows)}</div>' if rows else None


def present_attention(activity: AttentionActivity) -> DashboardItem:
    actor_id = activity.context.actor_id
    text = attention_text(activity)
    actor_name = activity.context.actor_name or str(actor_id)
    if activity.phase == "requested":
        css_class = "plan" if activity.attention_type == "plan" else "question"
        label = (
            f"{actor_name} ▸ proposes a plan"
            if css_class == "plan"
            else f"{actor_name} ▸ asks you"
        )
        label = html.escape(label)
        body = f'<div class="md">{md_html(text)}</div>'
    elif activity.attention_type == "plan":
        if activity.decision is None:
            raise ValueError("resolved attention requires a decision")
        decision = activity.decision
        css_decision = "changes" if decision == "changes_requested" else decision
        css_class = f"plandecision {css_decision}"
        label = {
            "approved": "you ▸ approved the plan",
            "changes_requested": "you ▸ asked for changes",
            "rejected": "you ▸ rejected the plan",
        }.get(decision, "you ▸ decided")
        edited = '<span class="pedit">edited before approval</span>' if activity.edited else ""
        body = f'<div class="md">{edited}{md_html(text)}</div>'
    else:
        if activity.attention_type is None:
            raise ValueError("resolved attention requires its request")
        css_class = "answer"
        label = "you ▸ answered"
        body = answer_html(activity) or f'<div class="md">{md_html(text)}</div>'
    markup = (
        f'<div class="msg {css_class}"><span class="who">{label}</span>'
        f'{body}</div>'
    )
    return DashboardItem(
        activity.context.activity_id,
        "attention",
        "attention",
        actor_id,
        (
            activity.outcome
            if activity.outcome in ("succeeded", "failed", "cancelled")
            else None
        ),
        markup,
        text,
        content_reference(activity.context.source_event_ids[0], "feedback", text)
        if activity.feedback is not None
        else None,
        conversation_kind=(
            "plan"
            if activity.phase == "requested" and activity.attention_type == "plan"
            else "question"
            if activity.phase == "requested"
            else "plan_decision"
            if activity.attention_type == "plan"
            else "answer"
        ),
        turn_id=activity.context.turn_id,
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
    )
