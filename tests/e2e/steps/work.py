"""Named work actions and worker checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from sdk.client import BaqylauClient
from sdk.state import AssignmentState, SessionSnapshot
from tests.e2e.testkit import turns as turn_checks
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import (
    SessionSpecs,
    Sessions,
    Turns,
    WorkerKind,
    WorkRef,
    WorkerControls,
    Works,
)
from tests.e2e.testkit.work import WorkDriver, WorkRequest


def _kind(value: str) -> WorkerKind:
    if value == "named subagent":
        return WorkerKind.SUBAGENT
    try:
        return WorkerKind(value)
    except ValueError as error:
        raise AssertionError(f"unknown worker type {value!r}") from error


def _bind_work(works: Works, turns: Turns, name: str, work: WorkRef) -> None:
    works.bind(name, work)
    turns.bind(name, work.turn)


@when(parsers.parse(
    'I launch session "{session_name}" and assign work "{work_name}" '
    'to the {worker_type} with prompt'
))
def launch_work(
    work_driver: WorkDriver,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    works: Works,
    session_name: str,
    work_name: str,
    worker_type: str,
    docstring: str,
) -> None:
    started = work_driver.launch(
        session_specs.get(session_name),
        work_name=work_name,
        worker_kind=_kind(worker_type),
        prompt=docstring.strip(),
    )
    sessions.bind(session_name, started.session)
    _bind_work(works, turns, work_name, started.work)


@when(parsers.parse(
    'I assign work "{work_name}" in session "{session_name}" '
    'to the {worker_type} with prompt'
))
def assign_work(
    work_driver: WorkDriver,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    works: Works,
    session_name: str,
    work_name: str,
    worker_type: str,
    docstring: str,
) -> None:
    work = work_driver.assign(
        session_specs.get(session_name),
        sessions.get(session_name),
        work_name=work_name,
        worker_kind=_kind(worker_type),
        prompt=docstring.strip(),
        named=worker_type == "named subagent",
    )
    _bind_work(works, turns, work_name, work)


@when(parsers.parse(
    'I request interruption of work "{work_name}" in session "{session_name}" '
    'as worker control "{control_name}"'
))
def interrupt_work(
    work_driver: WorkDriver,
    session_specs: SessionSpecs,
    works: Works,
    worker_controls: WorkerControls,
    work_name: str,
    session_name: str,
    control_name: str,
) -> None:
    work = works.get(work_name)
    worker_controls.bind(
        control_name,
        work_driver.interrupt(session_specs.get(session_name), work),
    )


@then(parsers.parse('worker control "{name}" request completes'))
def worker_control_request_completes(
    client: BaqylauClient,
    worker_controls: WorkerControls,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    control = worker_controls.get(name)
    if control.receipt is not None:
        assert control.receipt.status_code in (200, 202)
        assert control.receipt.outcome.status in ("acknowledged", "indeterminate")
        return
    if control.turn is None:
        raise AssertionError(f"worker control {name!r} has no request evidence")
    current = turn_checks.wait_until_complete(
        client,
        control.turn,
        name=name,
        timeout=wait_policy.turn,
    )
    answers = turn_checks.final_answer_texts(client, current)
    assert answers == ["INTERRUPT_SENT"]


@when(parsers.parse(
    'I launch session "{session_name}" as turn "{turn_name}" and assign these '
    'work items in parallel to subagents'
))
def launch_parallel_work(
    work_driver: WorkDriver,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    works: Works,
    session_name: str,
    turn_name: str,
    datatable: list[list[str]],
) -> None:
    if not datatable or datatable[0] != ["work", "prompt"]:
        raise AssertionError("parallel work table must have work and prompt columns")
    requests = tuple(
        WorkRequest(name.strip(), prompt.strip())
        for name, prompt in datatable[1:]
    )
    if any(not request.name or not request.prompt for request in requests):
        raise AssertionError("parallel work names and prompts must not be empty")
    started = work_driver.launch_parallel(
        session_specs.get(session_name),
        requests,
    )
    sessions.bind(session_name, started.session)
    turns.bind(turn_name, started.request_turn)
    for work_name, work in started.works:
        _bind_work(works, turns, work_name, work)


@when(parsers.parse(
    'I launch session "{session_name}" and assign work "{work_name}" to a '
    "subagent with follow-up '{followup}' using prompt"
))
def launch_work_with_followup(
    work_driver: WorkDriver,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    works: Works,
    session_name: str,
    work_name: str,
    followup: str,
    docstring: str,
) -> None:
    started = work_driver.launch_with_followup(
        session_specs.get(session_name),
        work_name=work_name,
        prompt=docstring.strip(),
        followup=followup,
    )
    sessions.bind(session_name, started.session)
    _bind_work(works, turns, work_name, started.work)


@when(parsers.parse(
    'I launch session "{session_name}" and assign work "{work_name}" to a '
    "subagent that sends '{message}' to the lead and returns '{result}'"
))
def launch_work_with_parent_message(
    work_driver: WorkDriver,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    works: Works,
    session_name: str,
    work_name: str,
    message: str,
    result: str,
) -> None:
    started = work_driver.launch_with_parent_message(
        session_specs.get(session_name),
        work_name=work_name,
        message=message,
        result=result,
    )
    sessions.bind(session_name, started.session)
    _bind_work(works, turns, work_name, started.work)


@then(parsers.parse('work "{name}" completes'))
def work_completes(
    client: BaqylauClient,
    works: Works,
    turns: Turns,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    work = works.get(name)
    if work.assignment is not None:
        def completed_assignment(snapshot: SessionSnapshot) -> bool | None:
            assignment = _assignment(snapshot, work)
            if assignment.state is None:
                return None
            if assignment.state != "succeeded":
                raise AssertionError(
                    f"subagent work {name!r} completed with state "
                    f"{assignment.state!r}"
                )
            if assignment.finished_cursor is None:
                return None
            return True

        client.sessions.watch(work.session).wait(
            f"subagent work {name!r} assignment to complete",
            completed_assignment,
            timeout=wait_policy.turn,
        )
        return
    current = turn_checks.wait_until_complete(
        client,
        turns.get(name),
        name=name,
        timeout=wait_policy.turn,
    )
    turns.replace(name, current)
    works.replace(
        name,
        WorkRef(
            work.session,
            work.requested_prompt,
            work.request_turn,
            work.worker,
            current,
            work.assignment,
        ),
    )


@then(parsers.parse('work "{name}" has final answer \'{text}\''))
def work_has_final_answer(
    client: BaqylauClient,
    works: Works,
    wait_policy: WaitPolicy,
    name: str,
    text: str,
) -> None:
    work = works.get(name)
    if work.assignment is not None:
        client.sessions.watch(work.session).wait(
            f"subagent work {name!r} to have final answer {text!r}",
            lambda snapshot: (
                True
                if turn_checks.matches_final_answer(
                    _assignment(snapshot, work).result,
                    text,
                )
                else None
            ),
            timeout=wait_policy.background,
        )
        return
    answers = turn_checks.final_answer_texts(client, work.turn)
    found = [
        answer for answer in answers
        if turn_checks.matches_final_answer(answer, text)
    ]
    assert len(found) == 1, (
        f"work {name!r} has {len(found)} final answers equal to {text!r}; "
        f"actual final answers: {answers}"
    )


@then(parsers.parse('work "{name}" has final answer containing \'{text}\''))
def work_has_final_answer_containing(
    client: BaqylauClient,
    works: Works,
    wait_policy: WaitPolicy,
    name: str,
    text: str,
) -> None:
    work = works.get(name)
    if work.assignment is not None:
        client.sessions.watch(work.session).wait(
            f"subagent work {name!r} to have a final answer containing {text!r}",
            lambda snapshot: (
                True
                if text in (_assignment(snapshot, work).result or "")
                else None
            ),
            timeout=wait_policy.background,
        )
        return
    answers = turn_checks.final_answer_texts(client, work.turn)
    found = [answer for answer in answers if text in answer]
    assert len(found) == 1, (
        f"work {name!r} has {len(found)} final answers containing {text!r}; "
        f"actual final answers: {answers}"
    )


@then(parsers.parse('work "{name}" has first final answer \'{text}\''))
def work_has_first_final_answer(
    client: BaqylauClient,
    works: Works,
    name: str,
    text: str,
) -> None:
    work = works.get(name)
    answers = turn_checks.final_answer_texts(client, work.turn)
    assert answers and turn_checks.matches_final_answer(answers[0], text), (
        f"work {name!r} first final answer is not {text!r}; "
        f"actual final answers: {answers}"
    )


@then(parsers.parse('work "{name}" has requested prompt \'{text}\''))
def work_has_requested_prompt(works: Works, name: str, text: str) -> None:
    assert works.get(name).requested_prompt == text


@then(parsers.parse('work "{name}" has worker type {worker_type}'))
def work_has_worker_type(
    client: BaqylauClient,
    works: Works,
    name: str,
    worker_type: str,
) -> None:
    work = works.get(name)
    expected = _kind(worker_type)
    snapshot = client.sessions.snapshot(work.session)
    actor = snapshot.actor(work.worker.actor_id)
    request_identity = work.request_turn.actor_id, work.request_turn.turn_id
    work_identity = work.turn.actor_id, work.turn.turn_id
    assert work.worker.kind == expected
    if expected == WorkerKind.LEAD:
        assert actor.parent_actor_id is None
        assert work.assignment is None
        assert work_identity == request_identity
    else:
        assert work.worker.parent_actor_id is not None
        assert actor.parent_actor_id == work.worker.parent_actor_id
        assert work.assignment is not None
        assert work.turn.actor_id == actor.actor_id
        assert work_identity != request_identity


@then(parsers.parse('work "{name}" has positive context use'))
def work_has_positive_context_use(
    client: BaqylauClient,
    works: Works,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    work = works.get(name)
    client.sessions.watch(work.session).wait(
        f"work {name!r} to report positive context use",
        lambda snapshot: (
            True
            if snapshot.actor(work.worker.actor_id).context.used_tokens > 0
            and snapshot.actor(work.worker.actor_id).context.window_tokens > 0
            else None
        ),
        timeout=wait_policy.feed,
    )


@then(parsers.parse('work "{name}" context use does not exceed its window'))
def work_context_use_does_not_exceed_window(
    client: BaqylauClient,
    works: Works,
    name: str,
) -> None:
    work = works.get(name)
    context = client.sessions.snapshot(work.session).actor(work.worker.actor_id).context
    assert 0 < context.used_tokens <= context.window_tokens


def _assignment(snapshot: SessionSnapshot, work: WorkRef) -> AssignmentState:
    if work.assignment is None:
        raise AssertionError("lead work does not have an assignment")
    found = [
        item
        for item in snapshot.assignments()
        if item.assignment_id == work.assignment.assignment_id
    ]
    if len(found) != 1:
        raise AssertionError(
            f"work assignment {work.assignment.assignment_id!r} has {len(found)} matches"
        )
    return found[0]


@then(parsers.parse('subagent work "{name}" has assignment state {state}'))
def subagent_work_has_assignment_state(
    client: BaqylauClient,
    works: Works,
    wait_policy: WaitPolicy,
    name: str,
    state: str,
) -> None:
    work = works.get(name)
    client.sessions.watch(work.session).wait(
        f"subagent work {name!r} to have assignment state {state!r}",
        lambda snapshot: True if _assignment(snapshot, work).state == state else None,
        timeout=wait_policy.background,
    )


@then(parsers.parse(
    'subagent work "{name}" is running while its lead has status {status}'
))
def running_subagent_has_lead_status(
    client: BaqylauClient,
    works: Works,
    wait_policy: WaitPolicy,
    name: str,
    status: str,
) -> None:
    work = works.get(name)
    if work.assignment is None:
        raise AssertionError(f"work {name!r} is not subagent work")

    def matches(snapshot: SessionSnapshot) -> bool | None:
        assignment = _assignment(snapshot, work)
        worker = snapshot.actor(work.worker.actor_id)
        if assignment.state is not None or worker.state != "running":
            return None
        return True if snapshot.lead().status == status else None

    client.sessions.watch(work.session).wait(
        f"subagent work {name!r} to run while its lead has status {status!r}",
        matches,
        timeout=wait_policy.background,
    )


@then(parsers.parse('work "{name}" has state {state}'))
def work_has_state(
    client: BaqylauClient,
    works: Works,
    wait_policy: WaitPolicy,
    name: str,
    state: str,
) -> None:
    work = works.get(name)

    def has_state(snapshot: SessionSnapshot) -> bool | None:
        if work.turn.turn_id is not None:
            return (
                True
                if snapshot.turn_state(work.turn.turn_id) == state
                else None
            )
        if state != "aborted" or work.assignment is None:
            return None
        assignment_state = _assignment(snapshot, work).state
        return True if assignment_state in ("cancelled", "failed") else None

    client.sessions.watch(work.session).wait(
        f"work {name!r} to have state {state!r}",
        has_state,
        timeout=wait_policy.turn,
    )


@then(parsers.parse("subagent work \"{name}\" has assignment result containing '{text}'"))
def subagent_work_has_assignment_result(
    client: BaqylauClient,
    works: Works,
    wait_policy: WaitPolicy,
    name: str,
    text: str,
) -> None:
    work = works.get(name)
    client.sessions.watch(work.session).wait(
        f"subagent work {name!r} result to contain {text!r}",
        lambda snapshot: True if text in _assignment(snapshot, work).result else None,
        timeout=wait_policy.background,
    )


@then(parsers.parse('work "{name}" releases the lead'))
def work_releases_lead(
    client: BaqylauClient,
    works: Works,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    work = works.get(name)

    def released(snapshot: SessionSnapshot) -> bool | None:
        lead = snapshot.lead()
        if work.assignment is None:
            return (
                True
                if lead.status == "awaiting_response" and not lead.statistics.active
                else None
            )
        assignment = _assignment(snapshot, work)
        if assignment.state is not None and assignment.state != "succeeded":
            raise AssertionError(
                f"subagent work {name!r} has assignment state {assignment.state!r}"
            )
        if assignment.state != "succeeded" or assignment.finished_cursor is None:
            return None
        return (
            True
            if lead.status == "awaiting_response" and not lead.statistics.active
            else None
        )

    client.sessions.watch(work.session).wait(
        f"work {name!r} to finish its assignment and release the lead",
        released,
        timeout=wait_policy.turn,
    )
