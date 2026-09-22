# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep the complete core feed vocabulary closed at the process boundary."""

from typing import Annotated

from pydantic import Field

from baqylau_extension_api.core import (
    entry_attention,
    entry_conversation,
    entry_extensions,
    entry_lifecycle,
    entry_resources,
    entry_shells,
)
from baqylau_extension_api.models.base import OpaqueId, WireModel

type CoreEntryBody = Annotated[
    entry_conversation.TurnStartedBody | entry_conversation.TurnFinishedBody
    | entry_conversation.MessageBody | entry_conversation.ReasoningBody
    | entry_shells.ShellStartedBody | entry_shells.ShellOutputBody
    | entry_shells.ShellBackgroundedBody | entry_shells.ShellFinishedBody
    | entry_resources.FileBody | entry_resources.SearchBody | entry_resources.WebBody
    | entry_resources.BrowserBody | entry_resources.WorktreeBody
    | entry_attention.SkillStartedBody | entry_attention.SkillFinishedBody
    | entry_attention.QuestionAskedBody | entry_attention.QuestionAnsweredBody
    | entry_attention.PlanProposedBody | entry_attention.PlanResolvedBody
    | entry_lifecycle.CompactionStartedBody | entry_lifecycle.CompactionFinishedBody
    | entry_lifecycle.AssignmentStartedBody | entry_lifecycle.AssignmentFinishedBody
    | entry_lifecycle.ModelChangeBody | entry_lifecycle.EffortChangeBody
    | entry_extensions.ExtensionBody,
    Field(discriminator="kind"),
]


class CoreSessionEntry(WireModel):
    """Carry a core feed proposal without a host-assigned commit cursor."""

    entry_id: OpaqueId
    session_id: OpaqueId
    actor_id: OpaqueId
    parent_actor_id: OpaqueId | None
    turn_id: OpaqueId | None
    occurred_at: float
    summary: str | None
    body: CoreEntryBody
