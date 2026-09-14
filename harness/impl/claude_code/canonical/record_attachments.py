# Copyright (c) 2026 Zhambyl Yermagambet
"""Record attachments."""

from __future__ import annotations

from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, Field

from harness.impl.claude_code.canonical.record_common import FOREIGN, OPEN_FOREIGN, ForeignMetadata
from harness.impl.claude_code.canonical.record_transcript_common import Origin
from harness.impl.claude_code.ids import (
    ClaudeCodeSessionId,
)

AttachmentBody = TypeVar("AttachmentBody", bound=BaseModel)


class GoalStatusAttachment(BaseModel):
    """Represent goal status attachment.

    An `attachment.type == "goal_status"` body — the one attachment kind
        parse_line reads a field of (`condition`/`met`/`reason`).
    """

    model_config = FOREIGN
    type: Literal["goal_status"] = "goal_status"
    condition: str | None = None
    met: bool | None = None
    reason: str | None = None
    duration_ms: Annotated[int | float | None, Field(alias="durationMs")] = None
    iterations: int | None = None
    sentinel: str | bool | None = None
    tokens: int | None = None


class QueuedCommandAttachment(BaseModel):
    """Represent queued command attachment.

    An `attachment.type == "queued_command"` body — the other attachment
        kind parse_line reads a field of (`commandMode`/`prompt`).
    """

    model_config = FOREIGN
    type: Literal["queued_command"] = "queued_command"
    command_mode: Annotated[str | None, Field(alias="commandMode")] = None
    prompt: str | None = None
    is_meta: Annotated[bool | None, Field(alias="isMeta")] = None
    origin: Origin | None = None
    source_uuid: str | None = None
    timestamp: str | None = None


class AttachmentHeader(BaseModel):
    """Represent attachment header."""

    model_config = OPEN_FOREIGN
    type: str | None = None


class RenderedAttachment(BaseModel):
    """Contain one text block rendered by Claude."""

    model_config = FOREIGN
    content: str


class AttachmentRecord[AttachmentBody: BaseModel](BaseModel):
    """Represent attachment record.

    A `type=attachment` record. Claude Code writes ~28 `attachment.type`
        values (corpus: `hook_success`, `total_tokens_reminder`, `skill_listing`,
        …); only `goal_status`/`queued_command` are read a field of, so the
        `attachment` body itself stays a JSON object here — `_attachment_body`
        (transcript.py) is what dispatches THOSE two into their own strict model,
        the same two-step "peek the discriminant, then validate" every other
        register in this module uses.
    """

    model_config = FOREIGN
    type: Literal["attachment"] = "attachment"
    attachment: AttachmentBody | None = None
    rendered: list[RenderedAttachment] | None = None
    # `renderedInHumanTurn` arrived with Claude Code 2.1.269 (observed
    # 2026-09-12) and holds the same blocks as `rendered`. It is listed because
    # this model FORBIDS an unknown field, and a record it refuses stops the
    # transcript of the session it is in.
    rendered_in_human_turn: Annotated[
        list[RenderedAttachment] | None, Field(alias="renderedInHumanTurn"),
    ] = None
    uuid: str | None = None
    parent_uuid: Annotated[str | None, Field(alias="parentUuid")] = None
    external_session_id: Annotated[str | None, Field(alias="sessionId")] = None
    session_id: ClaudeCodeSessionId | None = None
    timestamp: str | None = None
    cwd: str | None = None
    git_branch: Annotated[str | None, Field(alias="gitBranch")] = None
    entrypoint: str | None = None
    slug: str | None = None
    user_type: Annotated[str | None, Field(alias="userType")] = None
    version: str | None = None
    agent_id: Annotated[str | None, Field(alias="agentId")] = None
    is_sidechain: Annotated[bool | None, Field(alias="isSidechain")] = None


class QueueOperationRecord(BaseModel):
    """Represent queue operation record.

    A `type=queue-operation` record — the enqueue half of a
        task-notification's delivery. Most notifications also have a `user` copy.
        A child background command or a resumed agent's later completion can have
        only this copy.
    """

    model_config = FOREIGN
    type: Literal["queue-operation"] = "queue-operation"
    operation: str | None = None
    # The same <task-notification> XML string a `user` record's plain-string
    # content carries (transcript.py header) — a raw string here, not JSON.
    content: str | ForeignMetadata | None = None
    session_id: Annotated[str | None, Field(alias="sessionId")] = None
    timestamp: str | None = None
    # Claude Code adds this to `remove` records (currently
    # `absorbed_mid_turn`) when a queued prompt is consumed during a turn.
    reason: str | None = None


class TitleRecord(BaseModel):
    """Represent title record.

    An `agent-name` / `ai-title` / `summary` record — the three shapes
        transcript_metadata (messages.py) reads a naming fact out of. One model:
        each carries exactly one of the three text fields plus the two identity
        fields every transcript record type shares.
    """

    model_config = FOREIGN
    type: Literal["agent-name", "ai-title", "summary"]
    agent_name: Annotated[str | None, Field(alias="agentName")] = None
    ai_title: Annotated[str | None, Field(alias="aiTitle")] = None
    summary: str | None = None
    session_id: Annotated[str | None, Field(alias="sessionId")] = None
    uuid: str | None = None
