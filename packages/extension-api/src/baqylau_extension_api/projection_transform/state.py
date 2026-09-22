# Copyright (c) 2026 Zhambyl Yermagambet
"""Check core scopes without inventing absent actors in historical snapshots."""

from graphlib import CycleError, TopologicalSorter

from baqylau_extension_api.core.actor_state import CoreActorFacts
from baqylau_extension_api.core.aggregate import CoreAggregateState
from baqylau_extension_api.core.session_state import CoreSessionFacts
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest import rules
from baqylau_extension_api.models.projection_changes import CoreActorChange, CoreSessionChange, ProjectionChange
from baqylau_extension_api.models.scopes import ExtensionScope, SessionScope


def validate_core_state(scope: ExtensionScope, state: CoreAggregateState) -> None:
    """Require scoped rows and reject cycles among supplied actor references."""
    if state.session is not None:
        validate_session_scope(scope, state.session)
    rules.require_unique((actor.actor_id for actor in state.actors), "core snapshot actor identities")
    for actor in state.actors:
        validate_actor_scope(scope, actor)
    _validate_actor_graph(state.actors)


def validate_session_scope(scope: ExtensionScope, session: CoreSessionFacts) -> None:
    """Keep core session rows inside the selected session and harness.

    Raises:
        ExtensionContractError: If scope or identity differs.

    """
    if not isinstance(scope, SessionScope):
        message = "core session change requires a session scope"
        raise ExtensionContractError(message)
    if (session.session_id, session.harness) != (scope.session_id, scope.harness):
        message = "core session change must keep the selected session and harness"
        raise ExtensionContractError(message)


def validate_actor_scope(scope: ExtensionScope, actor: CoreActorFacts) -> None:
    """Keep actor rows inside the selected session without requiring one actor per batch.

    Raises:
        ExtensionContractError: If scope differs or an actor is its own parent.

    """
    if not isinstance(scope, SessionScope) or actor.session_id != scope.session_id:
        message = "core actor change must keep the selected session"
        raise ExtensionContractError(message)
    if actor.actor_id == actor.parent_actor_id:
        message = "core actor cannot be its own parent"
        raise ExtensionContractError(message)


def validate_proposed_state(
    scope: ExtensionScope, before: CoreAggregateState, changes: tuple[ProjectionChange, ...],
) -> None:
    """Check the combined actor graph without committing any proposed row."""
    session = next((
        change.session for change in changes if isinstance(change, CoreSessionChange)
    ), before.session)
    actors = {actor.actor_id: actor for actor in before.actors}
    for change in changes:
        if isinstance(change, CoreActorChange):
            actors[change.actor.actor_id] = change.actor
    validate_core_state(scope, CoreAggregateState(session=session, actors=tuple(actors.values())))


def _validate_actor_graph(actors: tuple[CoreActorFacts, ...]) -> None:
    graph: TopologicalSorter[str] = TopologicalSorter()
    for actor in actors:
        parents = () if actor.parent_actor_id is None else (actor.parent_actor_id,)
        graph.add(actor.actor_id, *parents)
    try:
        graph.prepare()
    except CycleError as error:
        message = "core snapshot actor references contain a cycle"
        raise ExtensionContractError(message) from error
