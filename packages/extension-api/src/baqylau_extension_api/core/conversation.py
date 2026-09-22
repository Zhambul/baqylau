# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish core turn, message, and reasoning payloads."""

from typing import Literal

from baqylau_extension_api.core.base import CorePayloadModel
from baqylau_extension_api.core.content import Content
from baqylau_extension_api.core.states import MessagePhase, MessageRole, Outcome
from baqylau_extension_api.models.base import OpaqueId


class TurnStarted(CorePayloadModel):
    """Record the prompt that starts a turn."""

    kind: Literal["turn.started"] = "turn.started"
    prompt_message_id: OpaqueId | None


class TurnFinished(CorePayloadModel):
    """Record a turn's final message and outcome."""

    kind: Literal["turn.finished"] = "turn.finished"
    final_message_id: OpaqueId | None
    outcome: Outcome


class TurnAborted(CorePayloadModel):
    """Record why a turn stopped."""

    kind: Literal["turn.aborted"] = "turn.aborted"
    reason: str | None


class MessageCreated(CorePayloadModel):
    """Record a message and its routing context."""

    kind: Literal["message.created"] = "message.created"
    message_id: OpaqueId
    role: MessageRole
    content: Content
    phase: MessagePhase | None
    reply_to: OpaqueId | None
    recipient_actor_id: OpaqueId | None = None


class MessageQueued(CorePayloadModel):
    """Record a message accepted into a harness queue."""

    kind: Literal["message.queued"] = "message.queued"
    request_id: OpaqueId
    content: Content


class ReasoningCreated(CorePayloadModel):
    """Record a visible reasoning block."""

    kind: Literal["reasoning.created"] = "reasoning.created"
    reasoning_id: OpaqueId
    content: Content
