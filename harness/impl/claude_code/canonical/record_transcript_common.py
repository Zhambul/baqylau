# Copyright (c) 2026 Zhambyl Yermagambet
"""Record transcript common."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from harness.impl.claude_code.canonical.record_common import FOREIGN, OPEN_FOREIGN
from harness.impl.claude_code.canonical.record_usage import MessageObject


class TranscriptDocument(BaseModel):
    """Common fields read independently of a transcript record's subtype."""

    model_config = OPEN_FOREIGN
    type: str | None = None
    uuid: str | None = None
    parent_uuid: Annotated[str | None, Field(alias="parentUuid")] = None
    timestamp: str | int | float | None = None
    cwd: str | None = None
    transcript_path: str | None = None
    message: MessageObject | None = None
    origin: Origin | None = None
    agent_name: Annotated[str | None, Field(alias="agentName")] = None
    ai_title: Annotated[str | None, Field(alias="aiTitle")] = None
    summary: str | None = None


class PreservedCompactSegment(BaseModel):
    """Represent preserved compact segment."""

    model_config = FOREIGN
    head_uuid: Annotated[str, Field(alias="headUuid")]
    anchor_uuid: Annotated[str, Field(alias="anchorUuid")]
    tail_uuid: Annotated[str, Field(alias="tailUuid")]


class PreservedCompactMessages(BaseModel):
    """Represent preserved compact messages."""

    model_config = FOREIGN
    anchor_uuid: Annotated[str, Field(alias="anchorUuid")]
    uuids: tuple[str, ...]
    all_uuids: Annotated[tuple[str, ...], Field(alias="allUuids")]


class CompactMetadata(BaseModel):
    """Represent compact metadata."""

    model_config = FOREIGN
    pre_tokens: Annotated[int | None, Field(alias="preTokens")] = None
    trigger: str | None = None
    post_tokens: Annotated[int | None, Field(alias="postTokens")] = None
    cumulative_dropped_tokens: Annotated[int | None, Field(alias="cumulativeDroppedTokens")] = None
    duration_ms: Annotated[int | float | None, Field(alias="durationMs")] = None
    pre_compact_discovered_tools: Annotated[tuple[str, ...] | None, Field(alias="preCompactDiscoveredTools")] = None
    preserved_segment: Annotated[PreservedCompactSegment | None, Field(alias="preservedSegment")] = None
    preserved_messages: Annotated[PreservedCompactMessages | None, Field(alias="preservedMessages")] = None


class HookSummaryInfo(BaseModel):
    """One command/prompt hook measured in a `stop_hook_summary` record."""

    model_config = FOREIGN
    command: str
    duration_ms: Annotated[int | float | None, Field(alias="durationMs")] = None
    prompt_text: Annotated[str | None, Field(alias="promptText")] = None


class Origin(BaseModel):
    """Represent origin.

    A `user` record's `origin` — read for `origin.kind == "task-notification"`
        (transcript.parse_line); the other fields ride along unread but are
        corpus-observed on the same object.
    """

    model_config = FOREIGN
    kind: str | None = None
    name: str | None = None
    sender_task_id: Annotated[str | None, Field(alias="senderTaskId")] = None
    body: str | None = None
    from_: str | None = Field(default=None, alias="from")
    # Claude Code 2.1.x names what queued a notification (corpus: "goal-checkin").
    source: str | None = None
