"""Canonical activity to dashboard-owned items and escaped HTML."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Literal

from dashboard.markdown import md_html
from domain.ids import ActorId, CanonicalEventId
from domain.values import Content, StructuredContent, TextContent
from runtime.projections import (
    Activity,
    AttentionActivity,
    ActorAssignmentActivity,
    CompactionActivity,
    FileActivity,
    MessageActivity,
    OperationActivity,
    ActorMessageActivity,
    ReasoningActivity,
    TaskActivity,
)


@dataclass(frozen=True)
class DashboardItem:
    item_id: str
    item_type: Literal[
        "message",
        "reasoning",
        "operation",
        "file",
        "attention",
        "task",
        "compaction",
        "actor_assignment",
        "actor_message",
    ]
    summary_kind: Literal[
        "message",
        "shell",
        "background",
        "monitor",
        "file_read",
        "file_write",
        "file_edit",
        "search",
        "network",
        "workspace",
        "media",
        "skill",
        "task",
        "message_delivery",
        "attention",
        "compaction",
        "actor_assignment",
        "actor_message",
    ]
    actor_id: ActorId
    state: Literal["running", "succeeded", "failed", "cancelled"] | None
    html: str
    plain_text: str
    content_reference: str | None
    conversation_kind: Literal[
        "prompt",
        "message",
        "system",
        "question",
        "answer",
        "plan",
        "plan_decision",
        "recap",
        "actor_message",
    ] | None = None
    turn_id: str | None = None
    final: bool = False
    actor_assignment_id: str | None = None
    actor_assignment_phase: Literal["started", "finished"] | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    message_id: str | None = None
    reply_to_message_id: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    command_reference: str | None = None
    output_reference: str | None = None
    file_path: str | None = None


def _plain(content: Content | None) -> str:
    if content is None:
        return ""
    if isinstance(content, TextContent):
        return content.text
    if isinstance(content, StructuredContent):
        return json.dumps(json.loads(content.json_text), ensure_ascii=False, indent=2, sort_keys=True)
    raise TypeError(f"unsupported content: {type(content).__name__}")


def _content_reference(event_id: CanonicalEventId, field: str, text: str) -> str | None:
    return f"{event_id}:{field}" if len(text) > 4096 else None


def _operation_text(activity: OperationActivity) -> str:
    return activity.output_text() or _plain(activity.arguments)


def _operation_summary_kind(activity: OperationActivity) -> str:
    if activity.execution in ("background", "monitor"):
        return activity.execution
    if activity.category == "message":
        return "message_delivery"
    if activity.category is None:
        raise ValueError(f"operation {activity.operation_id} has no canonical category")
    return activity.category


def _duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h{int(seconds % 3600 // 60):02d}m"
    return f"{int(seconds // 86400)}d{int(seconds % 86400 // 3600):02d}h"


def _outcome_attribute(state: str | None) -> str:
    if state == "succeeded":
        return "ok"
    if state in ("failed", "cancelled"):
        return "bad"
    return "run"


def _dashboard_block(
    *,
    header_html: str,
    summary: str,
    body_html: str,
    state: str | None,
    started_at: float | None,
    finished_at: float | None,
    note: bool = False,
    quiet: bool = False,
    tail_html: str = "",
) -> str:
    attributes = ['class="blk"', 'data-open="0"']
    if note:
        attributes.append('data-note="1"')
    if quiet:
        attributes.append('data-quiet="1"')
    attributes.append(f'data-out="{_outcome_attribute(state)}"')
    if quiet and state == "running" and started_at is not None:
        tail_html = (
            f'<span class="chip blive" data-anchor="{started_at}"></span>'
        )
    elif quiet and state is not None and finished_at is not None:
        verdict = "finished" if state == "succeeded" else state
        duration = (
            f" · {_duration(finished_at - started_at)}"
            if started_at is not None
            else ""
        )
        tail_html = f'<span class="cqt">{html.escape(verdict + duration)}</span>'
    return (
        f'<div {" ".join(attributes)}><div class="bhead">'
        f'<span class="bchips">{header_html}</span>'
        f'<span class="bsum">{html.escape(summary)}</span>'
        f'<span class="btail">{tail_html}</span><span class="blinks"></span>'
        f'</div><div class="bbody">{body_html}</div></div>'
    )


def _operation_html(activity: OperationActivity, text: str, state: str | None) -> str:
    title = activity.description or activity.native_name or activity.category or "operation"
    arguments = _plain(activity.arguments)
    if activity.category == "skill":
        skill_name = arguments.strip() or str(title)
        note_text = f"Skill({skill_name})"
        if state in ("failed", "cancelled"):
            note_text += " failed"
        note_html = (
            f'<div class="anote" data-out="{_outcome_attribute(state)}">'
            '<span class="anmark">⏺</span>'
            f'<span class="atext">{html.escape(note_text)}</span></div>'
        )
        body_text = text or arguments
        return _dashboard_block(
            header_html=note_html,
            summary="",
            body_html=f'<div class="md">{md_html(body_text)}</div>' if body_text else "",
            state=state,
            started_at=activity.context.started_at,
            finished_at=activity.context.finished_at,
            note=True,
        )
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
    return _dashboard_block(
        header_html=header_html,
        summary=summary,
        body_html=body_html,
        state=state,
        started_at=activity.context.started_at,
        finished_at=activity.context.finished_at,
        quiet=quiet,
    )


def _file_presentation(activity: FileActivity) -> tuple[str, str]:
    file = activity.file
    verb, color = {
        "read": ("Read", "rgb(97,175,239)"),
        "created": ("Write", "rgb(152,195,121)"),
        "updated": ("Update", "rgb(229,192,123)"),
        "deleted": ("Delete", "rgb(224,108,117)"),
        "renamed": ("Move", "rgb(229,192,123)"),
    }[file.action]
    if activity.outcome == "failed":
        color = "rgb(224,108,117)"
    escaped_path = html.escape(file.path)
    markup = (
        f'<span style="color:{color}">{verb}</span>'
        '<span style="color:rgb(92,99,112)">(</span>'
        f'<span style="color:rgb(171,178,191)">{escaped_path}</span>'
        '<span style="color:rgb(92,99,112)">)</span>'
    )
    text = f"{verb}({file.path})"
    counts = []
    if file.lines_added:
        counts.append((f"+{file.lines_added}", "rgb(152,195,121)"))
    if file.lines_removed:
        counts.append((f"-{file.lines_removed}", "rgb(224,108,117)"))
    if counts:
        markup += "  " + " ".join(
            f'<span style="color:{count_color}">{count}</span>'
            for count, count_color in counts
        )
        text += "  " + " ".join(count for count, _count_color in counts)
    return text, f'<pre class="opl">{markup}</pre>'


def _attention_text(activity: AttentionActivity) -> str:
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


def _answer_html(activity: AttentionActivity) -> str | None:
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


def _message_html(activity: MessageActivity, text: str) -> str:
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


class DashboardPresenter:
    def present(self, activity: Activity) -> DashboardItem:
        actor_id = activity.context.actor_id
        if isinstance(activity, MessageActivity):
            text = _plain(activity.content)
            return DashboardItem(
                activity.context.activity_id,
                "message",
                "message",
                actor_id,
                None,
                _message_html(activity, text),
                text,
                _content_reference(activity.context.source_event_ids[0], "content", text),
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
        if isinstance(activity, ReasoningActivity):
            text = _plain(activity.content)
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
                _content_reference(activity.context.source_event_ids[0], "content", text),
                conversation_kind="message",
                turn_id=str(activity.context.turn_id) if activity.context.turn_id else None,
                started_at=activity.context.started_at,
                finished_at=activity.context.finished_at,
            )
        if isinstance(activity, OperationActivity):
            text = _operation_text(activity)
            state = None
            if activity.state == "running":
                state = "running"
            elif activity.outcome in ("succeeded", "failed", "cancelled"):
                state = activity.outcome
            content_reference = None
            if activity.content_event_id is not None and activity.content_field is not None:
                content_reference = _content_reference(
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
                _operation_summary_kind(activity),
                actor_id,
                state,
                _operation_html(activity, text, state),
                text,
                content_reference,
                started_at=activity.context.started_at,
                finished_at=activity.context.finished_at,
                command_reference=command_reference,
                output_reference=output_reference,
            )
        if isinstance(activity, FileActivity):
            text, markup = _file_presentation(activity)
            return DashboardItem(
                activity.context.activity_id,
                "file",
                {
                    "read": "file_read",
                    "created": "file_write",
                    "updated": "file_edit",
                    "deleted": "file_edit",
                    "renamed": "file_edit",
                }[activity.file.action],
                actor_id,
                (
                    activity.outcome
                    if activity.outcome in ("succeeded", "failed", "cancelled")
                    else None
                ),
                markup,
                text,
                (
                    f"{activity.content_event_id}:{activity.content_field}"
                    if activity.content_event_id is not None and activity.content_field is not None
                    else None
                ),
                lines_added=activity.file.lines_added,
                lines_removed=activity.file.lines_removed,
                started_at=activity.context.started_at,
                finished_at=activity.context.finished_at,
                file_path=activity.file.path,
            )
        if isinstance(activity, AttentionActivity):
            text = _attention_text(activity)
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
                body = _answer_html(activity) or f'<div class="md">{md_html(text)}</div>'
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
                _content_reference(activity.context.source_event_ids[0], "feedback", text)
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
                turn_id=str(activity.context.turn_id) if activity.context.turn_id else None,
                started_at=activity.context.started_at,
                finished_at=activity.context.finished_at,
            )
        if isinstance(activity, TaskActivity):
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
        if isinstance(activity, CompactionActivity):
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
        if isinstance(activity, ActorAssignmentActivity):
            content = activity.result or activity.brief
            text = activity.reason or _plain(content)
            state = "running" if activity.state == "running" else activity.outcome
            item_state = state if state in ("running", "succeeded", "failed", "cancelled") else None
            phase = "started" if activity.state == "running" else "finished"
            duration = ""
            if activity.context.started_at is not None and activity.context.finished_at is not None:
                duration = f" · {_duration(activity.context.finished_at - activity.context.started_at)}"
            assignment_name = (
                _plain(activity.brief).strip()
                if activity.brief is not None
                else str(activity.assignment_id)
            )
            note_text = f'Agent "{assignment_name}" {phase}{duration}'
            note_html = (
                f'<div class="anote" data-out="{_outcome_attribute(item_state)}">'
                '<span class="anmark">⏺</span>'
                f'<span class="atext">{html.escape(note_text)}</span></div>'
            )
            content_reference = (
                _content_reference(activity.context.source_event_ids[-1], "result", text)
                if activity.result is not None
                else _content_reference(activity.context.source_event_ids[0], "brief", text)
                if activity.reason is None
                else None
            )
            return DashboardItem(
                activity.context.activity_id,
                "actor_assignment",
                "actor_assignment",
                actor_id,
                item_state,
                _dashboard_block(
                    header_html=note_html,
                    summary="",
                    body_html=f'<div class="md">{md_html(text)}</div>',
                    state=item_state,
                    started_at=activity.context.started_at,
                    finished_at=activity.context.finished_at,
                    note=True,
                ),
                text,
                content_reference,
                turn_id=str(activity.context.turn_id) if activity.context.turn_id else None,
                actor_assignment_id=str(activity.assignment_id),
                actor_assignment_phase="started" if activity.state == "running" else "finished",
                started_at=activity.context.started_at,
                finished_at=activity.context.finished_at,
            )
        if isinstance(activity, ActorMessageActivity):
            text = _plain(activity.content)
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
                _dashboard_block(
                    header_html=note_html,
                    summary="",
                    body_html=f'<div class="md">{md_html(text)}</div>',
                    state=None,
                    started_at=activity.context.started_at,
                    finished_at=activity.context.finished_at,
                    note=True,
                ),
                text,
                _content_reference(activity.context.source_event_ids[0], "content", text),
                conversation_kind="actor_message",
                turn_id=str(activity.context.turn_id) if activity.context.turn_id else None,
                message_id=str(activity.message_id),
                started_at=activity.context.started_at,
                finished_at=activity.context.finished_at,
            )
        raise TypeError(f"unsupported activity: {type(activity).__name__}")
