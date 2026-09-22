# Copyright (c) 2026 Zhambyl Yermagambet
"""Preserve complete typed session state across the extension boundary."""

from baqylau_extension_api.core.base import CoreModel
from baqylau_extension_api.core.derived_states import LifecycleState
from baqylau_extension_api.core.references import AccountReference
from baqylau_extension_api.core.states import GoalState, TaskState
from baqylau_extension_api.models.base import OpaqueId


class SessionGoal(CoreModel):
    """Keep the current goal and its stored state."""

    objective: str | None
    state: GoalState
    reason: str | None


class SessionTask(CoreModel):
    """Keep one task and its optional assigned actor."""

    task_id: OpaqueId
    subject: str
    description: str | None
    state: TaskState
    owner_actor_id: OpaqueId | None


class CoreSessionFacts(CoreModel):
    """Carry the complete session row without a host storage revision."""

    session_id: OpaqueId
    harness: OpaqueId
    state: LifecycleState
    working_directory: str
    started_at: float | None
    lead_actor_id: OpaqueId
    title: str | None = None
    finished_at: float | None = None
    account: AccountReference | None = None
    goal: SessionGoal | None = None
    tasks: tuple[SessionTask, ...] = ()
    continued_from: OpaqueId | None = None
    prompt_title_internal: str | None = None
    custom_title_internal: str | None = None
    automatic_title_internal: str | None = None
    summary_title_internal: str | None = None
    task_order_internal: tuple[OpaqueId, ...] = ()
