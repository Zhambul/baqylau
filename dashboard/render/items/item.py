"""One rendered item: the shape the browser draws, and the pieces all items share.

`DashboardItem` is what every activity becomes — an id, a kind, escaped HTML,
and the plain text behind it. The helpers below are the vocabulary the
per-activity modules build with: how a duration reads, what an outcome colours,
and the block frame (header · summary · body) that everything but a bare
message is drawn in.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from typing import Literal

from domain.ids import ActorId, CanonicalEventId
from domain.values import Content, StructuredContent, TextContent


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


def plain_text(content: Content | None) -> str:
    if content is None:
        return ""
    if isinstance(content, TextContent):
        return content.text
    if isinstance(content, StructuredContent):
        return json.dumps(json.loads(content.json_text), ensure_ascii=False, indent=2, sort_keys=True)
    raise TypeError(f"unsupported content: {type(content).__name__}")


def content_reference(event_id: CanonicalEventId, field: str, text: str) -> str | None:
    return f"{event_id}:{field}" if len(text) > 4096 else None


def duration_text(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h{int(seconds % 3600 // 60):02d}m"
    return f"{int(seconds // 86400)}d{int(seconds % 86400 // 3600):02d}h"


def outcome_attribute(state: str | None) -> str:
    if state == "succeeded":
        return "ok"
    if state in ("failed", "cancelled"):
        return "bad"
    return "run"


def block_html(
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
    attributes.append(f'data-out="{outcome_attribute(state)}"')
    if quiet and state == "running" and started_at is not None:
        tail_html = (
            f'<span class="chip blive" data-anchor="{started_at}"></span>'
        )
    elif quiet and state is not None and finished_at is not None:
        verdict = "finished" if state == "succeeded" else state
        duration = (
            f" · {duration_text(finished_at - started_at)}"
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
