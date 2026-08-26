"""Named question acquisition, answer actions, and question checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from api.sessiondata.models.entry import (
    MessageBodyResponse,
    QuestionResponse,
)
from sdk.client import BaqylauClient
from sdk.state import QuestionState, SessionSnapshot
from tests.e2e.testkit import selectors, turns as turn_checks
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.questions import QuestionWorkDriver
from tests.e2e.testkit.references import (
    Controls,
    QuestionRef,
    Questions,
    Sessions,
    SessionSpecs,
    Turns,
    WorkerKind,
    WorkRef,
    Works,
)


def _worker_kind(value: str) -> WorkerKind:
    try:
        return WorkerKind(value)
    except ValueError as error:
        raise AssertionError(f"unknown worker type {value!r}") from error


def _bind_question_work(
    works: Works,
    turns: Turns,
    work_name: str,
    work: WorkRef,
) -> None:
    works.bind(work_name, work)
    turns.bind(work_name, work.turn)


@when(parsers.parse(
    'I launch session "{session_name}" and assign question work "{work_name}" '
    'to the {worker_type} with prompt'
))
def launch_question_work(
    question_work_driver: QuestionWorkDriver,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    works: Works,
    session_name: str,
    work_name: str,
    worker_type: str,
    docstring: str,
) -> None:
    started = question_work_driver.launch(
        session_specs.get(session_name),
        work_name=work_name,
        worker_kind=_worker_kind(worker_type),
        prompt=docstring.strip(),
    )
    sessions.bind(session_name, started.session)
    _bind_question_work(works, turns, work_name, started.work)


@when(parsers.parse(
    'I assign question work "{work_name}" in session "{session_name}" '
    'to the {worker_type} with prompt'
))
def assign_question_work(
    question_work_driver: QuestionWorkDriver,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    works: Works,
    session_name: str,
    work_name: str,
    worker_type: str,
    docstring: str,
) -> None:
    work = question_work_driver.assign(
        session_specs.get(session_name),
        sessions.get(session_name),
        work_name=work_name,
        worker_kind=_worker_kind(worker_type),
        prompt=docstring.strip(),
    )
    _bind_question_work(works, turns, work_name, work)


def _question(
    snapshot: SessionSnapshot,
    reference: QuestionRef,
) -> tuple[QuestionState, QuestionResponse]:
    states = [
        item for item in snapshot.questions() if item.attention_id == reference.attention_id
    ]
    if len(states) != 1:
        raise AssertionError(
            f"question attention {reference.attention_id!r} has {len(states)} matches"
        )
    prompts = [
        item for item in states[0].questions if item.question_id == reference.question_id
    ]
    if len(prompts) != 1:
        raise AssertionError(f"question {reference.question_id!r} has {len(prompts)} matches")
    return states[0], prompts[0]


def choice_label_matches(observed: str, expected: str) -> bool:
    """Ignore only the recommendation badge native question tools may append."""
    return observed in (expected, f"{expected} (Recommended)")


@when(parsers.parse(
    'I name the pending question in turn "{turn_name}" containing '
    '\'{prompt}\' "{question_name}"'
))
@when(parsers.parse(
    'I name the pending question in work "{turn_name}" containing '
    '\'{prompt}\' "{question_name}"'
))
def name_pending_question(
    client: BaqylauClient,
    turns: Turns,
    questions: Questions,
    wait_policy: WaitPolicy,
    turn_name: str,
    prompt: str,
    question_name: str,
) -> None:
    original = turns.get(turn_name)
    turn = selectors.turn(
        client.sessions.watch(original.session),
        original,
        wait_policy.turn,
    )
    turns.replace(turn_name, turn)
    found = selectors.question(
        client.sessions.watch(turn.session),
        turn_reference=turn,
        turn_name=turn_name,
        prompt_contains=prompt,
        timeout=wait_policy.turn,
    )
    questions.bind(question_name, found)


@when(parsers.parse(
    'I answer question "{question_name}" with option \'{option}\' '
    'as control "{control_name}"'
))
def answer_question(
    client: BaqylauClient,
    questions: Questions,
    controls: Controls,
    turns: Turns,
    question_name: str,
    option: str,
    control_name: str,
) -> None:
    reference = questions.get(question_name)
    receipt = controls.bind(
        control_name,
        client.sessions.answer_question(
            reference.session,
            attention_id=reference.attention_id,
            answers=({"selected": [option], "other": ""},),
        ),
    )
    turns.replace(
        reference.turn_name,
        turns.get(reference.turn_name).resumed_after(receipt.cursor_before),
    )


@when(parsers.parse(
    'I answer question "{question_name}" with free text \'{answer}\' '
    'as control "{control_name}"'
))
def answer_question_with_free_text(
    client: BaqylauClient,
    questions: Questions,
    controls: Controls,
    turns: Turns,
    question_name: str,
    answer: str,
    control_name: str,
) -> None:
    reference = questions.get(question_name)
    receipt = controls.bind(
        control_name,
        client.sessions.answer_question(
            reference.session,
            attention_id=reference.attention_id,
            answers=({"selected": [], "other": answer},),
        ),
    )
    turns.replace(
        reference.turn_name,
        turns.get(reference.turn_name).resumed_after(receipt.cursor_before),
    )


@when(parsers.parse(
    'I answer questions "{first_name}" with option \'{first_answer}\' and '
    '"{second_name}" with option \'{second_answer}\' as control "{control_name}"'
))
def answer_two_questions(
    client: BaqylauClient,
    questions: Questions,
    controls: Controls,
    turns: Turns,
    first_name: str,
    first_answer: str,
    second_name: str,
    second_answer: str,
    control_name: str,
) -> None:
    named_answers = {
        questions.get(first_name): first_answer,
        questions.get(second_name): second_answer,
    }
    references = tuple(named_answers)
    first = references[0]
    if any(
        reference.session != first.session
        or reference.attention_id != first.attention_id
        or reference.turn_name != first.turn_name
        for reference in references[1:]
    ):
        raise AssertionError("named questions do not belong to one dialog")
    state, _prompt = _question(client.sessions.snapshot(first.session), first)
    expected_ids = {reference.question_id for reference in references}
    actual_ids = {prompt.question_id for prompt in state.questions}
    if expected_ids != actual_ids:
        raise AssertionError(
            "named questions must include every question in the dialog: "
            f"named {sorted(expected_ids)!r}; dialog {sorted(actual_ids)!r}"
        )
    answers_by_id = {
        reference.question_id: answer
        for reference, answer in named_answers.items()
    }
    receipt = controls.bind(
        control_name,
        client.sessions.answer_question(
            first.session,
            attention_id=first.attention_id,
            answers=tuple(
                {
                    "selected": [answers_by_id[prompt.question_id]],
                    "other": "",
                }
                for prompt in state.questions
            ),
        ),
    )
    turns.replace(
        first.turn_name,
        turns.get(first.turn_name).resumed_after(receipt.cursor_before),
    )


@when(parsers.parse(
    'I answer question "{question_name}" with options \'{first}\' and \'{second}\' '
    'as control "{control_name}"'
))
def answer_question_with_two_options(
    client: BaqylauClient,
    questions: Questions,
    controls: Controls,
    turns: Turns,
    question_name: str,
    first: str,
    second: str,
    control_name: str,
) -> None:
    reference = questions.get(question_name)
    receipt = controls.bind(
        control_name,
        client.sessions.answer_question(
            reference.session,
            attention_id=reference.attention_id,
            answers=({"selected": [first, second], "other": ""},),
        ),
    )
    turns.replace(
        reference.turn_name,
        turns.get(reference.turn_name).resumed_after(receipt.cursor_before),
    )


@when(parsers.parse(
    'I dismiss question "{question_name}" and send chat text \'{discussion}\' '
    'as control "{control_name}"'
))
def discuss_question(
    client: BaqylauClient,
    questions: Questions,
    controls: Controls,
    question_name: str,
    discussion: str,
    control_name: str,
) -> None:
    reference = questions.get(question_name)
    controls.bind(
        control_name,
        client.sessions.discuss_question(
            reference.session,
            attention_id=reference.attention_id,
            discussion=discussion,
        ),
    )


@then(parsers.parse('question "{name}" is single choice'))
def question_is_single_choice(
    client: BaqylauClient,
    questions: Questions,
    name: str,
) -> None:
    reference = questions.get(name)
    _state, prompt = _question(client.sessions.snapshot(reference.session), reference)
    assert not prompt.multiple


@then(parsers.parse('question "{name}" is multiple choice'))
def question_is_multiple_choice(
    client: BaqylauClient,
    questions: Questions,
    name: str,
) -> None:
    reference = questions.get(name)
    _state, prompt = _question(client.sessions.snapshot(reference.session), reference)
    assert prompt.multiple


@then(parsers.parse('question "{name}" offers option \'{option}\''))
def question_offers_option(
    client: BaqylauClient,
    questions: Questions,
    name: str,
    option: str,
) -> None:
    reference = questions.get(name)
    _state, prompt = _question(client.sessions.snapshot(reference.session), reference)
    labels = [item.label for item in prompt.choices]
    assert any(choice_label_matches(label, option) for label in labels), (
        f"question {name!r} offers {labels}"
    )


@then(parsers.parse('question "{name}" records option \'{option}\''))
def question_records_option(
    client: BaqylauClient,
    questions: Questions,
    wait_policy: WaitPolicy,
    name: str,
    option: str,
) -> None:
    reference = questions.get(name)

    def recorded(snapshot: SessionSnapshot) -> bool | None:
        state, _prompt = _question(snapshot, reference)
        if state.answers is None:
            return None
        found = [
            item
            for item in state.answers
            if item.question_id == reference.question_id and item.labels == (option,)
        ]
        return True if len(found) == 1 else None

    client.sessions.watch(reference.session).wait(
        f"question {name!r} to record option {option!r}",
        recorded,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('question "{name}" records free text \'{answer}\''))
def question_records_free_text(
    client: BaqylauClient,
    questions: Questions,
    wait_policy: WaitPolicy,
    name: str,
    answer: str,
) -> None:
    reference = questions.get(name)

    def recorded(snapshot: SessionSnapshot) -> bool | None:
        state, _prompt = _question(snapshot, reference)
        if state.answers is None:
            return None
        found = [
            item
            for item in state.answers
            if item.question_id == reference.question_id and item.labels == (answer,)
        ]
        return True if len(found) == 1 else None

    client.sessions.watch(reference.session).wait(
        f"question {name!r} to record free text {answer!r}",
        recorded,
        timeout=wait_policy.feed,
    )


@then(parsers.parse(
    'question "{name}" records options \'{first}\' and \'{second}\''
))
def question_records_two_options(
    client: BaqylauClient,
    questions: Questions,
    wait_policy: WaitPolicy,
    name: str,
    first: str,
    second: str,
) -> None:
    reference = questions.get(name)

    def recorded(snapshot: SessionSnapshot) -> bool | None:
        state, _prompt = _question(snapshot, reference)
        if state.answers is None:
            return None
        found = [
            item
            for item in state.answers
            if item.question_id == reference.question_id
            and set(item.labels) == {first, second}
        ]
        return True if len(found) == 1 else None

    client.sessions.watch(reference.session).wait(
        f"question {name!r} to record options {first!r} and {second!r}",
        recorded,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('question "{name}" is resolved'))
def question_is_resolved(
    client: BaqylauClient,
    questions: Questions,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    reference = questions.get(name)
    client.sessions.watch(reference.session).wait(
        f"question {name!r} to be resolved",
        lambda snapshot: True if not _question(snapshot, reference)[0].pending else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse(
    'question "{question_name}" is followed by final answer \'{text}\' '
    'after control "{control_name}"'
))
def question_is_followed_by_final_answer(
    client: BaqylauClient,
    questions: Questions,
    controls: Controls,
    wait_policy: WaitPolicy,
    question_name: str,
    text: str,
    control_name: str,
) -> None:
    reference = questions.get(question_name)
    control = controls.get(control_name)

    def exact_answer(snapshot: SessionSnapshot) -> bool | None:
        state, _prompt = _question(snapshot, reference)
        found = [
            entry
            for entry in snapshot.entries
            if entry.cursor > control.cursor_before
            and entry.actor_id == state.actor_id
            and isinstance(entry.body, MessageBodyResponse)
            and entry.body.role == "assistant"
            and entry.body.phase == "end_turn"
            and turn_checks.matches_final_answer(entry.body.content.text, text)
        ]
        if len(found) > 1:
            raise AssertionError(
                f"question {question_name!r} has {len(found)} final answers "
                f"equal to {text!r} after control {control_name!r}"
            )
        return True if len(found) == 1 else None

    client.sessions.watch(reference.session).wait(
        f"question {question_name!r} to be followed by final answer {text!r}",
        exact_answer,
        timeout=wait_policy.turn,
    )


@then(parsers.parse(
    'question "{question_name}" sends chat prompt \'{text}\' '
    'after control "{control_name}"'
))
def question_sends_chat_prompt(
    client: BaqylauClient,
    questions: Questions,
    controls: Controls,
    wait_policy: WaitPolicy,
    question_name: str,
    text: str,
    control_name: str,
) -> None:
    reference = questions.get(question_name)
    control = controls.get(control_name)

    def exact_prompt(snapshot: SessionSnapshot) -> bool | None:
        state, _prompt = _question(snapshot, reference)
        found = [
            entry
            for entry in snapshot.entries
            if entry.cursor > control.cursor_before
            and entry.actor_id == state.actor_id
            and isinstance(entry.body, MessageBodyResponse)
            and entry.body.role == "user"
            and entry.body.phase == "prompt"
            and turn_checks.matches_final_answer(entry.body.content.text, text)
        ]
        if len(found) > 1:
            raise AssertionError(
                f"question {question_name!r} has {len(found)} chat prompts "
                f"equal to {text!r} after control {control_name!r}"
            )
        return True if len(found) == 1 else None

    client.sessions.watch(reference.session).wait(
        f"question {question_name!r} to send chat prompt {text!r}",
        exact_prompt,
        timeout=wait_policy.feed,
    )
