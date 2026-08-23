"""Named question acquisition, answer actions, and question checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from api.sessiondata.models.entry import QuestionResponse
from sdk.client import BaqylauClient
from sdk.state import QuestionState, SessionSnapshot
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import Controls, QuestionRef, Questions, Turns


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


@when(parsers.parse(
    'I name the pending question in turn "{turn_name}" containing '
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
    assert option in labels, f"question {name!r} offers {labels}"


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
