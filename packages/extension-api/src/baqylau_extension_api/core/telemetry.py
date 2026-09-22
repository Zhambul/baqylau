# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish core usage, context, and compaction payloads."""

from decimal import Decimal
from typing import Literal

from baqylau_extension_api.core.base import CorePayloadModel
from baqylau_extension_api.core.content import Content
from baqylau_extension_api.core.references import AccountReference, ModelReference
from baqylau_extension_api.core.states import UsageScope
from baqylau_extension_api.core.usage import TokenUsage


class UsageReported(CorePayloadModel):
    """Record token use and exact decimal cost."""

    kind: Literal["usage.reported"] = "usage.reported"
    scope: UsageScope
    subject_id: str
    model: ModelReference | None
    account: AccountReference | None
    tokens: TokenUsage
    cumulative: bool
    cost_in_usd: Decimal | None


class ContextReported(CorePayloadModel):
    """Record context use for an actor."""

    kind: Literal["context.reported"] = "context.reported"
    used_tokens: int
    window_tokens: int
    model: ModelReference | None


class CompactionStarted(CorePayloadModel):
    """Record context size before compaction."""

    kind: Literal["compaction.started"] = "compaction.started"
    before_tokens: int | None


class CompactionFinished(CorePayloadModel):
    """Record context size and retained content after compaction."""

    kind: Literal["compaction.finished"] = "compaction.finished"
    before_tokens: int | None
    after_tokens: int | None
    context: Content | None = None
