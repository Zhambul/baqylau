# Copyright (c) 2026 Zhambyl Yermagambet
"""Preserve all typed actor state needed to validate derived-data changes."""

from decimal import Decimal
from typing import Annotated

from pydantic import Field

from baqylau_extension_api.core.base import CoreModel
from baqylau_extension_api.core.derived_states import ActorStatus, LifecycleState
from baqylau_extension_api.core.references import ModelReference
from baqylau_extension_api.core.states import ActorRole
from baqylau_extension_api.core.usage import TokenUsage
from baqylau_extension_api.models.base import OpaqueId, Revision


class ActorUsage(CoreModel):
    """Keep cumulative tokens and exact decimal cost."""

    tokens: TokenUsage = TokenUsage()
    cost_in_usd: Decimal | None = None


class ActorContext(CoreModel):
    """Keep the current context-window and compaction state."""

    used_tokens: Revision = 0
    window_tokens: Revision = 0
    compacting: bool = False


class ActorBackground(CoreModel):
    """Keep live work references separate from a display-only status."""

    running_shell_ids: tuple[OpaqueId, ...] = ()
    monitor_count: Revision = 0
    background_job_count: Revision = 0


class ToolCount(CoreModel):
    """Keep the count for one named tool."""

    tool: str
    count: Revision


class ActorStatistics(CoreModel):
    """Keep visible totals and the state used to calculate later totals."""

    prompt_count: Revision = 0
    shell_command_count: Revision = 0
    failed_shell_command_count: Revision = 0
    file_count: Revision = 0
    lines_added: Revision = 0
    lines_removed: Revision = 0
    actor_message_count: Revision = 0
    tool_counts: tuple[ToolCount, ...] = ()
    active_seconds: Annotated[float, Field(ge=0)] = Field(default_factory=float)
    active_since_internal: float | None = None
    file_paths_internal: tuple[str, ...] = ()


class CoreActorFacts(CoreModel):
    """Carry the complete actor row without a host storage revision."""

    session_id: OpaqueId
    actor_id: OpaqueId
    role: ActorRole
    name: str
    state: LifecycleState
    parent_actor_id: OpaqueId | None = None
    description: str | None = None
    started_at: float | None = None
    finished_at: float | None = None
    model: ModelReference | None = None
    effort: str | None = None
    status: ActorStatus | None = None
    usage: ActorUsage = ActorUsage()
    context: ActorContext = ActorContext()
    background: ActorBackground = ActorBackground()
    statistics: ActorStatistics = ActorStatistics()
    pending_attention_internal: tuple[OpaqueId, ...] = ()
    running_assignment_ids_internal: tuple[OpaqueId, ...] = ()
