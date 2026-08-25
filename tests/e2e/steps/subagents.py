"""Named subagent and assignment acquisition and checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from api.sessiondata.models.entry import MessageBodyResponse
from api.sessiondata.models.session_data import ActorResponse
from sdk.client import BaqylauClient
from sdk.state import AssignmentState, SessionSnapshot
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import (
    ActorRef,
    ActorMessages,
    Actors,
    AssignmentRef,
    Assignments,
    Sessions,
    Shells,
    Turns,
    Works,
)
from tests.e2e.testkit.turns import matches_final_answer


def _assignment(snapshot: SessionSnapshot, reference: AssignmentRef) -> AssignmentState:
    found = [item for item in snapshot.assignments() if item.assignment_id == reference.assignment_id]
    if len(found) != 1:
        raise AssertionError(f"assignment {reference.assignment_id!r} has {len(found)} matches")
    return found[0]


def _actor(snapshot: SessionSnapshot, reference: ActorRef) -> ActorResponse:
    return snapshot.actor(reference.actor_id)


@when(parsers.parse('I name the only assignment in turn "{turn_name}" "{assignment_name}"'))
def name_assignment(
    client: BaqylauClient,
    turns: Turns,
    assignments: Assignments,
    wait_policy: WaitPolicy,
    turn_name: str,
    assignment_name: str,
) -> None:
    turn = turns.get(turn_name)
    found = selectors.assignment(
        client.sessions.watch(turn.session),
        turn_reference=turn,
        timeout=wait_policy.feed,
    )
    assignments.bind(assignment_name, found)


@when(parsers.parse('I name the actor assigned to assignment "{assignment_name}" "{actor_name}"'))
def name_assigned_actor(
    client: BaqylauClient,
    assignments: Assignments,
    actors: Actors,
    wait_policy: WaitPolicy,
    assignment_name: str,
    actor_name: str,
) -> None:
    assignment = assignments.get(assignment_name)

    def assigned_actor(snapshot: SessionSnapshot) -> ActorRef | None:
        actor_id = _assignment(snapshot, assignment).actor_id
        if actor_id is None:
            return None
        actor = snapshot.actor(actor_id)
        return (
            ActorRef(assignment.session, actor_id)
            if actor.parent_actor_id is not None
            else None
        )

    found = client.sessions.watch(assignment.session).wait(
        f"assignment {assignment_name!r} to identify its actor",
        assigned_actor,
        timeout=wait_policy.feed,
    )
    actors.bind(actor_name, found)


@when(parsers.parse('I name the subagent in session "{session_name}" with exact name \'{exact_name}\' "{actor_name}"'))
def name_subagent(
    client: BaqylauClient,
    sessions: Sessions,
    actors: Actors,
    wait_policy: WaitPolicy,
    session_name: str,
    exact_name: str,
    actor_name: str,
) -> None:
    found = selectors.actor(
        client.sessions.watch(sessions.get(session_name)),
        exact_name=exact_name,
        timeout=wait_policy.feed,
    )
    actors.bind(actor_name, found)


@when(parsers.parse('I name the only command for actor "{actor_name}" containing \'{command}\' "{shell_name}"'))
def name_actor_command(
    client: BaqylauClient,
    actors: Actors,
    shells: Shells,
    wait_policy: WaitPolicy,
    actor_name: str,
    command: str,
    shell_name: str,
) -> None:
    actor = actors.get(actor_name)
    found = selectors.shell(
        client.sessions.watch(actor.session),
        actor_id=actor.actor_id,
        command_contains=command,
        timeout=wait_policy.feed,
    )
    shells.bind(shell_name, found)


@when(parsers.parse(
    'I name the exact message \'{text}\' sent to worker of work "{work_name}" '
    '"{message_name}"'
))
def name_message_to_work_worker(
    client: BaqylauClient,
    works: Works,
    actor_messages: ActorMessages,
    wait_policy: WaitPolicy,
    text: str,
    work_name: str,
    message_name: str,
) -> None:
    work = works.get(work_name)
    snapshot = client.sessions.snapshot(work.session)
    actor_messages.bind(
        message_name,
        selectors.actor_message(
            client.sessions.watch(work.session),
            sender_actor_id=snapshot.lead().actor_id,
            recipient_actor_id=work.worker.actor_id,
            exact_text=text,
            timeout=wait_policy.feed,
        ),
    )


@when(parsers.parse(
    'I name the exact message \'{text}\' sent by worker of work "{work_name}" '
    '"{message_name}"'
))
def name_message_from_work_worker(
    client: BaqylauClient,
    works: Works,
    actor_messages: ActorMessages,
    wait_policy: WaitPolicy,
    text: str,
    work_name: str,
    message_name: str,
) -> None:
    work = works.get(work_name)
    lead_actor_id = client.sessions.snapshot(work.session).lead().actor_id
    actor_messages.bind(
        message_name,
        selectors.actor_message(
            client.sessions.watch(work.session),
            sender_actor_id=work.worker.actor_id,
            recipient_actor_id=lead_actor_id,
            exact_text=text,
            timeout=wait_policy.feed,
        ),
    )


@then(parsers.parse(
    'actor message "{message_name}" goes from the lead to worker of work '
    '"{work_name}"'
))
def actor_message_goes_from_lead_to_work_worker(
    client: BaqylauClient,
    works: Works,
    actor_messages: ActorMessages,
    message_name: str,
    work_name: str,
) -> None:
    work = works.get(work_name)
    message = actor_messages.get(message_name)
    snapshot = client.sessions.snapshot(work.session)
    assert message.session == work.session
    assert message.sender_actor_id == snapshot.lead().actor_id
    assert message.recipient_actor_id == work.worker.actor_id


@then(parsers.parse(
    "follow-up '{text}' is observed by worker of work \"{work_name}\""
))
def followup_is_observed_by_work_worker(
    client: BaqylauClient,
    works: Works,
    wait_policy: WaitPolicy,
    text: str,
    work_name: str,
) -> None:
    """Accept each harness's durable proof that the child received a follow-up.

    Claude exposes SendMessage as a clear actor-to-actor message. Codex keeps
    collaboration payloads encrypted in its rollout, but exposes the child
    answer produced by the triggered turn. Both are feed-level evidence tied
    to the selected child actor, not a guess based on the lead's response.
    """
    work = works.get(work_name)
    lead_actor_id = client.sessions.snapshot(work.session).lead().actor_id
    answer_after = work.turn.activity_cursor or 0

    def observed(snapshot: SessionSnapshot) -> bool | None:
        sent = [
            entry
            for entry in snapshot.entries
            if entry.actor_id == lead_actor_id
            and isinstance(entry.body, MessageBodyResponse)
            and entry.body.recipient_actor_id == work.worker.actor_id
            and entry.body.content.text == text
        ]
        answered = [
            entry
            for entry in snapshot.entries
            if entry.cursor > answer_after
            and entry.actor_id == work.worker.actor_id
            and isinstance(entry.body, MessageBodyResponse)
            and entry.body.role == "assistant"
            and entry.body.phase == "end_turn"
            and matches_final_answer(entry.body.content.text, text)
        ]
        return True if sent or answered else None

    client.sessions.watch(work.session).wait(
        f"worker of work {work_name!r} to observe follow-up {text!r}",
        observed,
        timeout=wait_policy.feed,
    )


@then(parsers.parse(
    'actor message "{message_name}" goes from worker of work "{work_name}" '
    "to the lead"
))
def actor_message_goes_from_work_worker_to_lead(
    client: BaqylauClient,
    works: Works,
    actor_messages: ActorMessages,
    message_name: str,
    work_name: str,
) -> None:
    work = works.get(work_name)
    message = actor_messages.get(message_name)
    snapshot = client.sessions.snapshot(work.session)
    assert message.session == work.session
    assert message.sender_actor_id == work.worker.actor_id
    assert message.recipient_actor_id == snapshot.lead().actor_id


@then(parsers.parse('assignment "{name}" has state {state}'))
def assignment_has_state(
    client: BaqylauClient,
    assignments: Assignments,
    wait_policy: WaitPolicy,
    name: str,
    state: str,
) -> None:
    reference = assignments.get(name)
    client.sessions.watch(reference.session).wait(
        f"assignment {name!r} to have state {state!r}",
        lambda snapshot: True if _assignment(snapshot, reference).state == state else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse("assignment \"{name}\" has result containing '{text}'"))
def assignment_has_result(
    client: BaqylauClient,
    assignments: Assignments,
    wait_policy: WaitPolicy,
    name: str,
    text: str,
) -> None:
    reference = assignments.get(name)
    client.sessions.watch(reference.session).wait(
        f"assignment {name!r} result to contain {text!r}",
        lambda snapshot: True if text in _assignment(snapshot, reference).result else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('actor "{name}" has state {state}'))
def actor_has_state(
    client: BaqylauClient,
    actors: Actors,
    wait_policy: WaitPolicy,
    name: str,
    state: str,
) -> None:
    reference = actors.get(name)
    client.sessions.watch(reference.session).wait(
        f"actor {name!r} to have state {state!r}",
        lambda snapshot: True if _actor(snapshot, reference).state == state else None,
        timeout=wait_policy.background,
    )


@then(parsers.parse('turn "{turn_name}" has exactly {count:d} assignments'))
def turn_has_assignment_count(
    client: BaqylauClient,
    turns: Turns,
    wait_policy: WaitPolicy,
    turn_name: str,
    count: int,
) -> None:
    turn = turns.get(turn_name)

    def counted(snapshot: SessionSnapshot) -> bool | None:
        found = [
            item
            for item in snapshot.assignments()
            if item.turn_id == turn.turn_id or selectors.cursor_is_in_turn(snapshot, turn, item.started_cursor)
        ]
        if len(found) > count:
            raise AssertionError(f"turn {turn_name!r} has {len(found)} assignments: {found}")
        return True if len(found) == count else None

    client.sessions.watch(turn.session).wait(
        f"turn {turn_name!r} to have exactly {count} assignments",
        counted,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('session "{session_name}" has exactly {count:d} subagents'))
def session_has_subagent_count(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    session_name: str,
    count: int,
) -> None:
    session = sessions.get(session_name)

    def counted(snapshot: SessionSnapshot) -> bool | None:
        found = [item for item in snapshot.data.actors if item.parent_actor_id is not None]
        if len(found) > count:
            raise AssertionError(f"session {session_name!r} has {len(found)} subagents")
        return True if len(found) == count else None

    client.sessions.watch(session).wait(
        f"session {session_name!r} to have exactly {count} subagents",
        counted,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('every subagent in session "{session_name}" has state {state}'))
def every_subagent_has_state(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    session_name: str,
    state: str,
) -> None:
    session = sessions.get(session_name)

    def finished(snapshot: SessionSnapshot) -> bool | None:
        found = [item for item in snapshot.data.actors if item.parent_actor_id is not None]
        return True if found and all(item.state == state for item in found) else None

    client.sessions.watch(session).wait(
        f"every subagent in session {session_name!r} to have state {state!r}",
        finished,
        timeout=wait_policy.background,
    )


@then(parsers.parse("the lead actor in session \"{session_name}\" has no command containing '{command}'"))
def lead_has_no_command(
    client: BaqylauClient,
    sessions: Sessions,
    session_name: str,
    command: str,
) -> None:
    snapshot = client.sessions.snapshot(sessions.get(session_name))
    found = [item.command for item in snapshot.shells(actor_id=snapshot.lead().actor_id)]
    assert not any(command in item for item in found), f"lead actor has a command containing {command!r}: {found}"
