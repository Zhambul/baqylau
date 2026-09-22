# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep execution identity and calculation state out of display-only changes."""

from baqylau_extension_api.core.actor_state import ActorBackground, CoreActorFacts
from baqylau_extension_api.core.base import CoreModel
from baqylau_extension_api.core.derived_states import LifecycleState
from baqylau_extension_api.core.session_state import CoreSessionFacts
from baqylau_extension_api.core.states import ActorRole
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.base import OpaqueId


class _SessionExecution(CoreModel):
    session_id: OpaqueId
    harness: OpaqueId
    state: LifecycleState
    working_directory: str
    started_at: float | None
    lead_actor_id: OpaqueId
    finished_at: float | None
    continued_from: OpaqueId | None
    prompt_title_internal: str | None
    custom_title_internal: str | None
    automatic_title_internal: str | None
    summary_title_internal: str | None
    task_order_internal: tuple[OpaqueId, ...]


class _ContextExecution(CoreModel):
    compacting: bool


class _StatisticsExecution(CoreModel):
    active_since_internal: float | None
    file_paths_internal: tuple[str, ...]


class _ActorExecution(CoreModel):
    session_id: OpaqueId
    actor_id: OpaqueId
    role: ActorRole
    state: LifecycleState
    parent_actor_id: OpaqueId | None
    started_at: float | None
    finished_at: float | None
    background: ActorBackground
    context: _ContextExecution
    statistics: _StatisticsExecution
    pending_attention_internal: tuple[OpaqueId, ...]
    running_assignment_ids_internal: tuple[OpaqueId, ...]


def require_session_execution(expected: CoreSessionFacts | None, actual: CoreSessionFacts) -> None:
    """Keep session identity, lifecycle, and calculation state unchanged.

    Raises:
        ExtensionContractError: If the change needs a canonical lifecycle event.

    """
    if expected is None or _SessionExecution.model_validate(expected) != _SessionExecution.model_validate(actual):
        message = "projection transform cannot create, remove, or change session execution state"
        raise ExtensionContractError(message)


def require_actor_execution(expected: CoreActorFacts | None, actual: CoreActorFacts) -> None:
    """Keep actor identity, live work, and calculation state unchanged.

    Raises:
        ExtensionContractError: If the change needs a canonical lifecycle event.

    """
    if expected is None or _ActorExecution.model_validate(expected) != _ActorExecution.model_validate(actual):
        message = "projection transform cannot create, remove, or change actor execution state"
        raise ExtensionContractError(message)
