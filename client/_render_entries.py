# Copyright (c) 2026 Zhambyl Yermagambet
"""Split terminal mirror rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING

from _render_numbers import count
from _render_rows import rows
from _render_styles import (
    MODIFIED,
    PLAN_DECISIONS,
    TEXT,
    USER,
    WORKING,
    Span,
)
from _render_tasks import _said
from _render_tools import _entry_text

if TYPE_CHECKING:
    from _model import (
        EntryBodyRecord,
        EntryRecord,
        SessionModel,
    )


def _message_rows(entry: EntryRecord, model: SessionModel, width: int) -> list[str]:
    body = entry.body
    name = model.actor_name(entry.actor_id)
    if body.phase == "recap":
        label, color = "RECAP", TEXT
    elif body.role == "user":
        label, color = "YOU", USER
    elif body.role == "parent":
        label, color = "PARENT", TEXT
    elif body.recipient_actor_id:
        recipient_name = model.actor_name(body.recipient_actor_id).upper()
        label = f"{name.upper()} → {recipient_name}"
        color = USER
    else:
        label, color = name.upper(), TEXT
    return _said(label, _entry_text(body.content), width, color)


def _attention_rows(entry: EntryRecord, width: int) -> list[str]:
    kind = entry.type
    if kind == "question_asked":
        return _said("QUESTION", _question_text(entry), width, WORKING)
    if kind == "plan_proposed":
        return _said("PLAN", _entry_text(entry.body.plan), width, WORKING)
    if kind == "question_answered":
        answered = "\n".join(label for answer in entry.body.answers for label in answer.labels)
        return _said("ANSWERED", entry.body.feedback or answered, width, WORKING)
    decision = PLAN_DECISIONS.get(entry.body.state or "", "DECIDED")
    return _said(decision, entry.body.feedback or "", width, WORKING)


def _question_text(entry: EntryRecord) -> str:
    blocks = []
    for question in entry.body.questions:
        lines = [question.question] if question.question else []
        lines.extend(f"- {choice.label}" for choice in question.choices if choice.label)
        if lines:
            blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _note_rows(entry: EntryRecord, width: int) -> list[str]:
    """Return the note rows.

    The one-line ⟳ and ⇢ forms: something happened that is worth a line and
        no more.

    Returns:
        Note rows.

    """
    text = _note_text(entry)
    return rows([Span("⟳ ", MODIFIED), Span(text)], width)


def _note_text(entry: EntryRecord) -> str:
    body = entry.body
    kind = entry.type
    if kind == "compaction_started":
        return "compacting the context…"
    if kind == "compaction_finished":
        before, after = body.before_tokens, body.after_tokens
        text = "compacted"
        if before and after:
            text = f"{text} · {count(before)} → {count(after)} tokens"
        return text
    if kind == "extension":
        # The pane has no extension code: a stored entry shows its summary, or its owner and type.
        return entry.summary or f"{body.owner}: {body.entry_type}"
    if kind == "model_change":
        text = f"model {_transition(body)}"
        if body.automatic:
            text = f"{text} (chosen for you)"
        return text
    return f"effort {_transition(body)}"


def _transition(body: EntryBodyRecord) -> str:
    previous, current = body.previous, body.current or ""
    return f"{previous} → {current}" if previous else current
