# Copyright (c) 2026 Zhambyl Yermagambet
"""Select stable actor references."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from api.sessiondata.models.entry import MessageBodyResponse
from tests.e2e.testkit import references as refs
from tests.e2e.testkit.selector_common import _one

if TYPE_CHECKING:
    from sdk.client import SessionWatch
    from sdk.state import SessionSnapshot


def _find_actor(snapshot: SessionSnapshot, exact_name: str) -> refs.ActorRef | None:
    candidates = [
        actor_state
        for actor_state in snapshot.session_data.actors
        if actor_state.parent_actor_id is not None and actor_state.name.casefold() == exact_name.casefold()
    ]
    actor_state = _one(candidates, f"subagent named {exact_name!r}")
    if actor_state is None:
        return None
    return refs.ActorRef(snapshot.session_reference, actor_state.actor_id)


def actor(watch: SessionWatch, *, exact_name: str, timeout: float) -> refs.ActorRef:
    """Find one child actor with the specified name.

    Returns:
        The child actor reference.

    """
    return watch.wait(
        f"one subagent named {exact_name!r}",
        partial(_find_actor, exact_name=exact_name),
        timeout=timeout,
    )


def _find_actor_message(
    snapshot: SessionSnapshot,
    sender_actor_id: str,
    recipient_actor_id: str,
    exact_text: str,
) -> refs.ActorMessageRef | None:
    candidates = [
        entry
        for entry in snapshot.entries
        if entry.actor_id == sender_actor_id
        and isinstance(entry.body, MessageBodyResponse)
        and entry.body.recipient_actor_id == recipient_actor_id
        and entry.body.content.text == exact_text
    ]
    message_entry = _one(
        candidates,
        f"actor message from {sender_actor_id!r} to {recipient_actor_id!r} with text {exact_text!r}",
    )
    if message_entry is None or not isinstance(message_entry.body, MessageBodyResponse):
        return None
    return refs.ActorMessageRef(
        snapshot.session_reference,
        message_entry.entry_id,
        message_entry.actor_id,
        message_entry.body.recipient_actor_id or "",
        message_entry.body.content.text,
    )


def actor_message(
    watch: SessionWatch,
    *,
    sender_actor_id: str,
    recipient_actor_id: str,
    exact_text: str,
    timeout: float,
) -> refs.ActorMessageRef:
    """Find one exact message between two actors.

    Returns:
        The actor message reference.

    """
    return watch.wait(
        f"one actor message from {sender_actor_id!r} to {recipient_actor_id!r}",
        partial(
            _find_actor_message,
            sender_actor_id=sender_actor_id,
            recipient_actor_id=recipient_actor_id,
            exact_text=exact_text,
        ),
        timeout=timeout,
    )


def _find_actor_from_assignment(
    snapshot: SessionSnapshot,
    assignment_reference: refs.AssignmentRef,
) -> refs.ActorRef | None:
    assignments = [
        assignment_state
        for assignment_state in snapshot.assignments()
        if assignment_state.assignment_id == assignment_reference.assignment_id
    ]
    assignment_state = _one(assignments, f"assignment {assignment_reference.assignment_id!r}")
    if assignment_state is None or assignment_state.actor_id is None:
        return None
    # The assignment can name its child before the child's own start fact arrives.
    if not any(actor.actor_id == assignment_state.actor_id for actor in snapshot.session_data.actors):
        return None
    candidate = snapshot.actor(assignment_state.actor_id)
    if candidate.parent_actor_id is None:
        return None
    return refs.ActorRef(snapshot.session_reference, candidate.actor_id)


def actor_from_assignment(
    watch: SessionWatch,
    *,
    assignment_reference: refs.AssignmentRef,
    timeout: float,
) -> refs.ActorRef:
    """Find the child actor for an assignment.

    Returns:
        The child actor reference.

    """
    return watch.wait(
        f"assignment {assignment_reference.assignment_id!r} to identify its child actor",
        partial(_find_actor_from_assignment, assignment_reference=assignment_reference),
        timeout=timeout,
    )
