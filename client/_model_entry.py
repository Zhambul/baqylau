# Copyright (c) 2026 Zhambyl Yermagambet
"""Model entry."""

from __future__ import annotations

from _model_actor import ActorRecord
from _model_attention import AnswerRecord, QuestionRecord
from _model_base import ContentRecord, SessionRecord, WireModel
from pydantic import Field


class EntryBodyRecord(WireModel):
    prompt_message_id: str | None = None
    message_id: str = ""
    reasoning_id: str = ""
    shell_id: str = ""
    command: ContentRecord | None = None
    execution: str | None = None
    content: ContentRecord | None = None
    stream: str | None = None
    mode: str | None = None
    state: str | None = None
    exit_code: int | None = None
    result: ContentRecord | None = None
    path: str | None = None
    previous_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    action: str | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    tool: str | None = None
    query: ContentRecord | None = None
    url: str | None = None
    arguments: ContentRecord | None = None
    name: str | None = None
    skill_id: str = ""
    phase: str | None = None
    role: str | None = None
    recipient_actor_id: str | None = None
    reply_to: str | None = None
    attention_id: str | None = None
    questions: tuple[QuestionRecord, ...] = ()
    plan: ContentRecord | None = None
    answers: tuple[AnswerRecord, ...] = ()
    feedback: str | None = None
    edited: bool = False
    before_tokens: int | None = None
    after_tokens: int | None = None
    automatic: bool = False
    previous: str | None = None
    current: str | None = None
    assigned_actor_name: str | None = None
    assignment_id: str = ""
    prompt: ContentRecord | None = None
    owner: str | None = None
    entry_type: str | None = None


class EntryRecord(WireModel):
    entry_id: str
    type: str
    cursor: int
    actor_id: str
    parent_actor_id: str | None = None
    turn_id: str | None = None
    occurred_at: float
    summary: str | None = None
    body: EntryBodyRecord


class SnapshotDocument(WireModel):
    cursor: int
    session: SessionRecord
    actors: tuple[ActorRecord, ...]
    live: bool = False


class EntryPageDocument(WireModel):
    entries: tuple[EntryRecord, ...] = Field(alias="items")
    oldest_cursor: int = 0
    has_more: bool = False


class StreamFrameDocument(WireModel):
    session: SessionRecord | None = None
    actors: tuple[ActorRecord, ...] = ()
    entries: tuple[EntryRecord, ...] = ()
