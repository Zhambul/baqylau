# Copyright (c) 2026 Zhambyl Yermagambet
"""Actor status work."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from domain import (
    actor_state,
    event_actor,
    event_conversation,
    event_resource,
    event_shell,
    messaging,
)

if TYPE_CHECKING:
    from domain import event_base
    from engine.sessiondata import contract


def _finished_turn_actor(actor: actor_state.ActorFacts) -> actor_state.ActorFacts:
    """Settle the actor of a turn that ended, and drop its attentions.

    A harness cannot end a turn while its dialog waits, so an attention that
    outlives its turn has no one left to answer it. This is the only resolution
    left when the source lost the tool join of the aborted call, and the turn
    end always carries it.

    Returns:
        The settled actor.

    """
    has_background = actor.background.running_shell_ids or actor.running_assignment_ids_internal
    status = (
        actor_state.ActorStatus.AWAITING_BACKGROUND if has_background else actor_state.ActorStatus.AWAITING_RESPONSE
    )
    return replace(actor, status=status, pending_attention_internal=())


def _clear_actor_statuses(aggregate_state: contract.AggregateState) -> contract.AggregateState:
    # The session is over: nobody is doing anything, and every actor
    # should say so rather than keep the last thing it was doing.
    idle_actors = {}
    for actor_id, actor in aggregate_state.actors.items():
        idle_actors[actor_id] = replace(actor, status=None)
    return aggregate_state.with_actors(idle_actors)


def _is_new_actor(payload: event_base.EventPayload, actor: actor_state.ActorFacts) -> bool:
    return isinstance(payload, event_actor.ActorStarted) and actor.status is None


def _is_prompt(event_payload: event_base.EventPayload) -> bool:
    return (
        isinstance(event_payload, event_conversation.MessageCreated)
        and event_payload.role == messaging.MessageRole.USER
        and event_payload.phase == messaging.MessagePhase.PROMPT
    )


def _is_finished_work(event_payload: event_base.EventPayload) -> bool:
    """Return whether finished work.

    Work that ended and was not background. Every one of these was one
        `operation.finished` before the operation abstraction dissolved, and the
        file and search ones arrive only at result time now — which is the same
        branch they used to land on twice.

    Returns:
        Whether finished work.

    """
    return isinstance(
        event_payload,
        (
            event_shell.ShellFinished,
            event_resource.SkillFinished,
            event_resource.FileAccessed,
            event_resource.SearchPerformed,
            event_resource.WebFetched,
            event_resource.BrowserInteracted,
            event_resource.WorktreeChanged,
        ),
    )


def _status_after_work_settled(actor_facts: actor_state.ActorFacts) -> actor_state.ActorStatus:
    """Do not reopen an actor whose turn already ended.

    Native result registers can arrive after the turn-finished register. The
    active interval is the stable boundary: a result resumes ordinary work only
    inside an active turn. Outside one, it settles to the applicable waiting
    state.

    Returns:
        The actor status.

    """
    if actor_facts.pending_attention_internal:
        return actor_state.ActorStatus.AWAITING_ATTENTION
    if actor_facts.statistics.active_since_internal is not None:
        return actor_state.ActorStatus.WORKING
    if actor_facts.background.running_shell_ids or actor_facts.running_assignment_ids_internal:
        return actor_state.ActorStatus.AWAITING_BACKGROUND
    return actor_state.ActorStatus.AWAITING_RESPONSE
