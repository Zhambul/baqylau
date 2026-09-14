# Copyright (c) 2026 Zhambyl Yermagambet
"""Actor status writer."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, override

from domain import (
    actor_state,
    event_conversation,
    event_resource,
    event_session,
    event_shell,
    event_telemetry,
    event_work,
)
from engine.sessiondata import contract

if TYPE_CHECKING:
    from domain import event_base

from engine.sessiondata.actor_status_attention import _assignment_status, _attention_status
from engine.sessiondata.actor_status_background import _shell_started, _with_background
from engine.sessiondata.actor_status_work import (
    _clear_actor_statuses,
    _finished_turn_actor,
    _is_finished_work,
    _is_new_actor,
    _is_prompt,
    _status_after_work_settled,
)


class StatusWriter(contract.SessionDataWriter):
    """The one word an actor's tab colour and list row are painted from.

    A replay, not a rule engine: the last fact wins, and the ORDER of the
    branches below is the whole semantics. Two sets carry between events — the
    background commands still running, and the attentions still unanswered —
    because two of the branches ask a question about the past that no single
    event can answer.

    The one asymmetry worth naming: a command's finish does NOT end background
    work. A background job's launch reports finished immediately, while its
    output still flows, so ending it there emptied the set before a turn could
    ever end on it — which is how `awaiting_background` became unreachable and a
    session with a job still running read as idle. `shell.output_finished` is
    what ends background work.
    """

    @override
    def write(
        self,
        canonical_event: event_base.CanonicalEvent[event_base.EventPayload],
        aggregate_state: contract.AggregateState,
    ) -> contract.AggregateState:
        """Write the current actor status.

        Returns:
            The aggregate state.

        """
        event = canonical_event
        payload = event.payload
        if isinstance(payload, event_session.SessionFinished):
            return _clear_actor_statuses(aggregate_state)
        actor = aggregate_state.actor(event.actor_id)
        if actor is None:
            return aggregate_state
        early_actor = _early_status_actor(actor, payload)
        if early_actor is not None:
            return aggregate_state.with_actor(early_actor)
        return _late_status(aggregate_state, event, actor, payload)


def _early_status_actor(
    actor: actor_state.ActorFacts, payload: event_base.EventPayload,
) -> actor_state.ActorFacts | None:
    if isinstance(payload, event_session.SessionStarted) or _is_new_actor(payload, actor):
        return replace(actor, status=actor_state.ActorStatus.IDLE)
    if isinstance(payload, event_conversation.TurnStarted) or _is_prompt(payload):
        return replace(actor, status=actor_state.ActorStatus.THINKING)
    if isinstance(payload, event_conversation.ReasoningCreated):
        return replace(actor, status=actor_state.ActorStatus.WORKING)
    return _execution_status_actor(actor, payload)


def _execution_status_actor(
    actor: actor_state.ActorFacts, payload: event_base.EventPayload,
) -> actor_state.ActorFacts | None:
    if isinstance(payload, event_shell.ShellStarted):
        return _shell_started(actor, payload)
    if isinstance(payload, (event_resource.SkillStarted, event_work.TaskChanged, event_work.TaskListChanged)):
        return replace(actor, status=actor_state.ActorStatus.EXECUTING)
    if isinstance(payload, event_shell.ShellBackgrounded):
        return _with_background(actor, payload.shell_id, counts_as_job=True)
    return None


def _late_status(
    aggregate_state: contract.AggregateState,
    event: event_base.CanonicalEvent[event_base.EventPayload],
    actor: actor_state.ActorFacts,
    payload: event_base.EventPayload,
) -> contract.AggregateState:
    attention_state = _attention_status(aggregate_state, actor, payload)
    if attention_state is not None:
        return attention_state
    assignment_state = _assignment_status(aggregate_state, event, actor, payload)
    if assignment_state is not None:
        return assignment_state
    if isinstance(payload, event_telemetry.CompactionStarted):
        return aggregate_state.with_actor(replace(actor, status=actor_state.ActorStatus.WORKING))
    if isinstance(payload, event_telemetry.CompactionFinished) or _is_finished_work(payload):
        return aggregate_state.with_actor(
            replace(actor, status=_status_after_work_settled(actor)),
        )
    if isinstance(payload, (event_conversation.TurnFinished, event_conversation.TurnAborted)):
        state = aggregate_state.with_actor(_finished_turn_actor(actor))
    else:
        state = aggregate_state
    return state
