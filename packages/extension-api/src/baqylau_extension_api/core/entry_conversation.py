# Copyright (c) 2026 Zhambyl Yermagambet
"""Publish core turn, message, and reasoning feed bodies."""

from typing import Literal

from baqylau_extension_api.core.content import Content
from baqylau_extension_api.core.derived_states import TurnState
from baqylau_extension_api.core.entry_base import CoreEntryBodyModel
from baqylau_extension_api.core.states import MessagePhase, MessageRole
from baqylau_extension_api.models.base import OpaqueId


class TurnStartedBody(CoreEntryBodyModel):
    """Mark the start of one agent turn."""

    kind: Literal["turn_started"] = "turn_started"
    prompt_message_id: OpaqueId | None = None


class TurnFinishedBody(CoreEntryBodyModel):
    """Keep the state of a completed or aborted turn."""

    kind: Literal["turn_finished"] = "turn_finished"
    state: TurnState


class MessageBody(CoreEntryBodyModel):
    """Keep message content and its typed participant references."""

    kind: Literal["message"] = "message"
    message_id: OpaqueId
    role: MessageRole
    phase: MessagePhase | None
    content: Content
    recipient_actor_id: OpaqueId | None = None
    reply_to: OpaqueId | None = None


class ReasoningBody(CoreEntryBodyModel):
    """Keep visible reasoning with its original content type."""

    kind: Literal["reasoning"] = "reasoning"
    reasoning_id: OpaqueId
    content: Content
