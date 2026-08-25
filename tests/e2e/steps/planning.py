"""Named task acquisition and session planning checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from api.controls.models.control_outcome_response import PlanChoicesResultResponse
from api.sessiondata.models.session_data import TaskResponse
from domain.values import GoalState
from sdk.client import BaqylauClient
from sdk.state import PlanState, SessionSnapshot
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.planning import PlanWorkDriver, plan_state, wait_for_plan_answer
from tests.e2e.testkit.references import (
    Controls,
    PlanRef,
    Plans,
    Sessions,
    SessionSpecs,
    TaskRef,
    Tasks,
    Turns,
    Works,
)


def _task(snapshot: SessionSnapshot, reference: TaskRef) -> TaskResponse:
    found = [
        item for item in snapshot.data.session.tasks if item.task_id == reference.task_id
    ]
    if len(found) != 1:
        raise AssertionError(f"task {reference.task_id!r} has {len(found)} matches")
    return found[0]


def _plan(snapshot: SessionSnapshot, reference: PlanRef) -> PlanState:
    return plan_state(snapshot, reference)


@when(parsers.parse(
    'I start plan work "{turn_name}" in session "{session_name}" with prompt'
))
def start_plan_work(
    plan_work_driver: PlanWorkDriver,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    session_name: str,
    turn_name: str,
    docstring: str,
) -> None:
    turns.bind(
        turn_name,
        plan_work_driver.start(
            session_specs.get(session_name),
            sessions.get(session_name),
            docstring.strip(),
        ),
    )


@when(parsers.parse(
    'I name the pending plan in turn "{turn_name}" containing '
    '\'{text}\' "{plan_name}"'
))
def name_pending_plan(
    client: BaqylauClient,
    turns: Turns,
    plans: Plans,
    wait_policy: WaitPolicy,
    turn_name: str,
    text: str,
    plan_name: str,
) -> None:
    original = turns.get(turn_name)
    turn = selectors.turn(
        client.sessions.watch(original.session),
        original,
        wait_policy.turn,
    )
    turns.replace(turn_name, turn)
    plans.bind(
        plan_name,
        selectors.plan(
            client.sessions.watch(turn.session),
            turn_reference=turn,
            turn_name=turn_name,
            text_contains=text,
            timeout=wait_policy.turn,
        ),
    )


@when(parsers.parse(
    'I read choices for plan "{plan_name}" as control "{control_name}"'
))
def read_plan_choices(
    client: BaqylauClient,
    plans: Plans,
    controls: Controls,
    plan_name: str,
    control_name: str,
) -> None:
    reference = plans.get(plan_name)
    controls.bind(
        control_name,
        client.sessions.read_plan_choices(reference.session, reference.attention_id),
    )


@when(parsers.parse(
    'I choose plan option containing \'{label}\' from control "{choices_name}" '
    'for plan "{plan_name}" as control "{control_name}"'
))
def choose_plan_option(
    client: BaqylauClient,
    plans: Plans,
    controls: Controls,
    turns: Turns,
    label: str,
    choices_name: str,
    plan_name: str,
    control_name: str,
) -> None:
    outcome = controls.get(choices_name).outcome
    if not isinstance(outcome, PlanChoicesResultResponse):
        raise AssertionError(f"control {choices_name!r} has no plan choices")
    matches = [
        choice for choice in outcome.choices
        if label.casefold() in choice.label.casefold() and not choice.feedback
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"plan choice containing {label!r} has {len(matches)} matches: {outcome.choices}"
        )
    reference = plans.get(plan_name)
    receipt = controls.bind(
        control_name,
        client.sessions.decide_plan(
            reference.session,
            attention_id=reference.attention_id,
            decision=matches[0].digit,
        ),
    )
    turns.replace(
        reference.turn_name,
        turns.get(reference.turn_name).resumed_after(receipt.cursor_before),
    )


@when(parsers.parse(
    'I approve plan "{plan_name}" from control "{choices_name}" '
    'as control "{control_name}"'
))
def approve_plan(
    client: BaqylauClient,
    plans: Plans,
    controls: Controls,
    turns: Turns,
    plan_name: str,
    choices_name: str,
    control_name: str,
) -> None:
    outcome = controls.get(choices_name).outcome
    if not isinstance(outcome, PlanChoicesResultResponse):
        raise AssertionError(f"control {choices_name!r} has no plan choices")
    choices = [choice for choice in outcome.choices if not choice.feedback]
    if not choices:
        raise AssertionError(f"control {choices_name!r} has no approval choice")
    reference = plans.get(plan_name)
    receipt = controls.bind(
        control_name,
        client.sessions.decide_plan(
            reference.session,
            attention_id=reference.attention_id,
            decision=choices[0].digit,
        ),
    )
    turns.replace(
        reference.turn_name,
        turns.get(reference.turn_name).resumed_after(receipt.cursor_before),
    )


@when(parsers.parse(
    'I dismiss plan "{plan_name}" as control "{control_name}"'
))
def dismiss_plan(
    client: BaqylauClient,
    plans: Plans,
    controls: Controls,
    plan_name: str,
    control_name: str,
) -> None:
    reference = plans.get(plan_name)
    controls.bind(
        control_name,
        client.sessions.decide_plan(
            reference.session,
            attention_id=reference.attention_id,
            decision="dismiss",
        ),
    )


@when(parsers.parse(
    'I request plan changes \'{feedback}\' for plan "{plan_name}" '
    'as control "{control_name}"'
))
def request_plan_changes(
    client: BaqylauClient,
    plans: Plans,
    controls: Controls,
    feedback: str,
    plan_name: str,
    control_name: str,
) -> None:
    reference = plans.get(plan_name)
    controls.bind(
        control_name,
        client.sessions.decide_plan(
            reference.session,
            attention_id=reference.attention_id,
            decision="feedback",
            feedback=feedback,
        ),
    )


@then(parsers.parse('plan "{name}" contains \'{text}\''))
def plan_contains(client: BaqylauClient, plans: Plans, name: str, text: str) -> None:
    reference = plans.get(name)
    assert text in _plan(client.sessions.snapshot(reference.session), reference).text


@then(parsers.parse('control "{name}" offers a plan option containing \'{label}\''))
def control_offers_plan_option(controls: Controls, name: str, label: str) -> None:
    outcome = controls.get(name).outcome
    assert isinstance(outcome, PlanChoicesResultResponse)
    assert any(label.casefold() in choice.label.casefold() for choice in outcome.choices)


@then(parsers.parse('control "{name}" offers an approval plan option'))
def control_offers_approval_plan_option(controls: Controls, name: str) -> None:
    outcome = controls.get(name).outcome
    assert isinstance(outcome, PlanChoicesResultResponse)
    assert any(not choice.feedback for choice in outcome.choices)


@then(parsers.parse('plan "{name}" has state {state}'))
def plan_has_state(
    client: BaqylauClient,
    plans: Plans,
    wait_policy: WaitPolicy,
    name: str,
    state: str,
) -> None:
    reference = plans.get(name)
    client.sessions.watch(reference.session).wait(
        f"plan {name!r} to have state {state!r}",
        lambda snapshot: True if _plan(snapshot, reference).state == state else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('plan "{name}" has feedback \'{feedback}\''))
def plan_has_feedback(
    client: BaqylauClient,
    plans: Plans,
    wait_policy: WaitPolicy,
    name: str,
    feedback: str,
) -> None:
    reference = plans.get(name)
    client.sessions.watch(reference.session).wait(
        f"plan {name!r} to have feedback {feedback!r}",
        lambda snapshot: (
            True if _plan(snapshot, reference).feedback == feedback else None
        ),
        timeout=wait_policy.feed,
    )


@then(parsers.parse(
    'plan "{plan_name}" is followed by final answer \'{text}\' '
    'after control "{control_name}"'
))
def plan_is_followed_by_final_answer(
    client: BaqylauClient,
    plans: Plans,
    controls: Controls,
    wait_policy: WaitPolicy,
    plan_name: str,
    text: str,
    control_name: str,
) -> None:
    reference = plans.get(plan_name)
    control = controls.get(control_name)
    wait_for_plan_answer(
        client,
        reference,
        after_cursor=control.cursor_before,
        text=text,
        name=plan_name,
        timeout=wait_policy.turn,
    )


@when(parsers.parse(
    'I name the task in session "{session_name}" with subject '
    '\'{subject}\' "{task_name}"'
))
def name_task(
    client: BaqylauClient,
    sessions: Sessions,
    tasks: Tasks,
    wait_policy: WaitPolicy,
    session_name: str,
    subject: str,
    task_name: str,
) -> None:
    found = selectors.task(
        client.sessions.watch(sessions.get(session_name)),
        exact_subject=subject,
        timeout=wait_policy.feed,
    )
    tasks.bind(task_name, found)


@then(parsers.parse('task "{name}" has state {state}'))
def task_has_state(
    client: BaqylauClient,
    tasks: Tasks,
    wait_policy: WaitPolicy,
    name: str,
    state: str,
) -> None:
    reference = tasks.get(name)
    client.sessions.watch(reference.session).wait(
        f"task {name!r} to have state {state!r}",
        lambda snapshot: True if _task(snapshot, reference).state == state else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('task "{task_name}" belongs to worker of work "{work_name}"'))
def task_belongs_to_work_worker(
    client: BaqylauClient,
    tasks: Tasks,
    works: Works,
    task_name: str,
    work_name: str,
) -> None:
    reference = tasks.get(task_name)
    task = _task(client.sessions.snapshot(reference.session), reference)
    assert task.owner_actor_id == works.get(work_name).worker.actor_id


@then(parsers.parse("task \"{name}\" has description '{description}'"))
def task_has_description(
    client: BaqylauClient,
    tasks: Tasks,
    name: str,
    description: str,
) -> None:
    reference = tasks.get(name)
    assert _task(client.sessions.snapshot(reference.session), reference).description == description


@then(parsers.parse('session "{name}" has exactly {count:d} tasks'))
def session_has_task_count(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
    count: int,
) -> None:
    session = sessions.get(name)
    client.sessions.watch(session).wait(
        f"session {name!r} to have exactly {count} tasks",
        lambda snapshot: True if len(snapshot.data.session.tasks) == count else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('session "{name}" has goal \'{objective}\''))
def session_has_goal(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
    objective: str,
) -> None:
    session = sessions.get(name)
    client.sessions.watch(session).wait(
        f"session {name!r} to have goal {objective!r}",
        lambda snapshot: (
            True
            if snapshot.data.session.goal is not None
            and snapshot.data.session.goal.objective == objective
            else None
        ),
        timeout=wait_policy.feed,
    )


@then(parsers.parse('the goal in session "{name}" is complete'))
def session_goal_is_complete(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    session = sessions.get(name)
    client.sessions.watch(session).wait(
        f"the goal in session {name!r} to be complete",
        lambda snapshot: (
            True
            if snapshot.data.session.goal is not None
            and snapshot.data.session.goal.completed
            else None
        ),
        timeout=wait_policy.feed,
    )


@then(parsers.parse('the goal in session "{name}" has state {state}'))
def session_goal_has_state(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
    state: str,
) -> None:
    try:
        expected = GoalState(state)
    except ValueError:
        raise AssertionError(f"unknown goal state {state!r}") from None
    session = sessions.get(name)
    client.sessions.watch(session).wait(
        f"the goal in session {name!r} to have state {state!r}",
        lambda snapshot: (
            True
            if snapshot.data.session.goal is not None
            and snapshot.data.session.goal.state == expected
            else None
        ),
        timeout=wait_policy.feed,
    )
