"""What was said: a prompt, a reply, a thought, a word to another actor."""

from __future__ import annotations

import html

from dashboard.render.items.item import (
    DashboardItem,
    block_html,
    content_reference,
    plain_text,
)
from dashboard.render.markdown import md_html
from engine.projections import ActorMessageActivity, MessageActivity, ReasoningActivity


def message_html(activity: MessageActivity, text: str) -> str:
    actor_name = activity.context.actor_name or str(activity.context.actor_id)
    if activity.phase == "recap":
        css_class = "recap"
        label = "↩ recap"
        attributes = ""
    elif activity.role == "user" and activity.phase == "synthetic":
        css_class = "prompt sys"
        label = "⚙ system"
        attributes = ""
    elif activity.role == "user":
        css_class = "prompt"
        label = 'you<button class="rw" title="rewind to here">↶</button>'
        attributes = f' data-txt="{html.escape(text, quote=True)}"'
        if activity.reply_to is not None:
            attributes += f' data-par="{html.escape(str(activity.reply_to), quote=True)}"'
    elif activity.role == "assistant":
        css_class = "message"
        label = html.escape(actor_name)
        attributes = ""
    elif activity.role == "parent":
        css_class = "message"
        label = "parent agent"
        attributes = ""
    else:
        css_class = "prompt sys"
        label = "⚙ system"
        attributes = ""
    return (
        f'<div class="msg {css_class}"{attributes}><span class="who">{label}</span>'
        f'<div class="md">{md_html(text)}</div></div>'
    )


def present_message(activity: MessageActivity) -> DashboardItem:
    actor_id = activity.context.actor_id
    text = plain_text(activity.content)
    return DashboardItem(
        activity.context.activity_id,
        "message",
        "message",
        actor_id,
        None,
        message_html(activity, text),
        text,
        content_reference(activity.context.source_event_ids[0], "content", text),
        conversation_kind=(
            "recap"
            if activity.phase == "recap"
            else "prompt"
            if activity.role == "user"
            else "message"
            if activity.role in ("assistant", "parent")
            else "system"
        ),
        turn_id=str(activity.context.turn_id) if activity.context.turn_id else None,
        final=activity.phase == "final",
        message_id=str(activity.message_id),
        reply_to_message_id=str(activity.reply_to) if activity.reply_to else None,
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
    )


def present_reasoning(activity: ReasoningActivity) -> DashboardItem:
    actor_id = activity.context.actor_id
    text = plain_text(activity.content)
    actor_name = activity.context.actor_name or str(actor_id)
    return DashboardItem(
        activity.context.activity_id,
        "reasoning",
        "message",
        actor_id,
        None,
        (
            '<div class="msg message">'
            f'<span class="who">{html.escape(actor_name)}</span>'
            f'<div class="md">{md_html(text)}</div></div>'
        ),
        text,
        content_reference(activity.context.source_event_ids[0], "content", text),
        conversation_kind="message",
        turn_id=str(activity.context.turn_id) if activity.context.turn_id else None,
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
    )


def present_actor_message(activity: ActorMessageActivity) -> DashboardItem:
    actor_id = activity.context.actor_id
    text = plain_text(activity.content)
    sender = activity.context.actor_name or str(actor_id)
    preview_lines = text.strip().splitlines()
    preview = preview_lines[0] if preview_lines else ""
    note_text = f"Message {sender} → {activity.recipient_actor_id}"
    if preview:
        note_text += f": {preview}"
    note_html = (
        '<div class="anote"><span class="anmark">⏺</span>'
        f'<span class="atext">{html.escape(note_text)}</span></div>'
    )
    return DashboardItem(
        activity.context.activity_id,
        "actor_message",
        "actor_message",
        actor_id,
        None,
        block_html(
            header_html=note_html,
            summary="",
            body_html=f'<div class="md">{md_html(text)}</div>',
            state=None,
            started_at=activity.context.started_at,
            finished_at=activity.context.finished_at,
            note=True,
        ),
        text,
        content_reference(activity.context.source_event_ids[0], "content", text),
        conversation_kind="actor_message",
        turn_id=str(activity.context.turn_id) if activity.context.turn_id else None,
        message_id=str(activity.message_id),
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
    )
