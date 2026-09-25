# Copyright (c) 2026 Zhambyl Yermagambet
"""Record usage."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from harness.impl.claude_code.canonical.record_common import FOREIGN, ForeignMetadata
from harness.impl.claude_code.canonical.record_content_extra import MessageContentBlock
from harness.impl.claude_code.canonical.record_usage_base import (
    UsageCacheCreation,
    UsageIteration,
    UsageOutputTokensDetails,
    UsageServerToolUse,
    UsageServiceTier,
    UsageSpeed,
)


class UsageInferenceGeo(StrEnum):
    """Represent usage inference geo."""

    NOT_AVAILABLE = "not_available"


class MessageUsage(BaseModel):
    """Represent message usage."""

    model_config = FOREIGN
    input_tokens: int | float | None = None
    cache_creation_input_tokens: int | float | None = None
    cache_read_input_tokens: int | float | None = None
    output_tokens: int | float | None = None
    output_tokens_details: UsageOutputTokensDetails | None = None
    server_tool_use: UsageServerToolUse | None = None
    service_tier: UsageServiceTier | None = None
    cache_creation: UsageCacheCreation | None = None
    inference_geo: UsageInferenceGeo | None = None
    iterations: tuple[UsageIteration, ...] | None = None
    speed: UsageSpeed | None = None


class MessageObject(BaseModel):
    """Represent message object.

    The `message` object a `user`/`assistant` transcript record carries —
        one shape shared by both (corpus: the assistant's `usage`/`model`/
        `stop_reason` sit beside the same `id`/`role`/`content` a user message
        carries, just usually empty on the user side).
    """

    model_config = FOREIGN
    id: str | None = None
    type: str | None = None
    role: str | None = None
    model: str | None = None
    content: str | list[MessageContentBlock] | None = None
    stop_reason: str | None = None
    stop_sequence: str | None = None
    stop_details: ForeignMetadata | None = None
    usage: MessageUsage | None = None
    container: ForeignMetadata | None = None
    context_management: ForeignMetadata | None = None
    diagnostics: ForeignMetadata | None = None
    # Claude Code 2.1.x carries the model's input transformations beside the
    # message (corpus: always an empty list so far). A record it refuses loses
    # the whole message, so the field is read even when empty.
    input_transformations: list[ForeignMetadata] | None = None
