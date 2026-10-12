# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that check named question state and answers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pytest_bdd import parsers, then

from tests.e2e.testkit import question_states

if TYPE_CHECKING:
    from sdk.client import BaqylauClient
    from tests.e2e.testkit import question_contexts
    from tests.e2e.testkit.references import Questions


@then(parsers.parse('question "{name}" is single choice'))
def question_is_single_choice(client: BaqylauClient, questions: Questions, name: str) -> None:
    """Check that a question is single choice."""
    reference = questions.get(name)
    _state, prompt = question_states.question(client.sessions.snapshot(reference.session), reference)
    assert not prompt.multiple


@then(parsers.parse('question "{name}" is multiple choice'))
def question_is_multiple_choice(client: BaqylauClient, questions: Questions, name: str) -> None:
    """Check that a question is multiple choice."""
    reference = questions.get(name)
    _state, prompt = question_states.question(client.sessions.snapshot(reference.session), reference)
    assert prompt.multiple


@then(parsers.parse('question "{name}" accepts free text'))
def question_accepts_free_text(client: BaqylauClient, questions: Questions, name: str) -> None:
    """Check that a question accepts a typed answer."""
    reference = questions.get(name)
    _state, prompt = question_states.question(client.sessions.snapshot(reference.session), reference)
    assert prompt.free_text


@then(parsers.parse('question "{name}" accepts no free text'))
def question_accepts_no_free_text(client: BaqylauClient, questions: Questions, name: str) -> None:
    """Check that a question accepts only its choices."""
    reference = questions.get(name)
    _state, prompt = question_states.question(client.sessions.snapshot(reference.session), reference)
    assert not prompt.free_text


@then(parsers.parse("question \"{name}\" offers option '{option}'"))
def question_offers_option(client: BaqylauClient, questions: Questions, name: str, option: str) -> None:
    """Check that a question offers one option."""
    reference = questions.get(name)
    _state, prompt = question_states.question(client.sessions.snapshot(reference.session), reference)
    labels = [choice.label for choice in prompt.choices]
    assert any(question_states.choice_label_matches(label, option) for label in labels), (
        f"question {name!r} offers {labels}"
    )


@then(parsers.parse('question "{name}" is resolved'))
def question_is_resolved(question_observation_context: question_contexts.QuestionObservationContext, name: str) -> None:
    """Wait for one resolved question dialog."""
    reference = question_observation_context.questions.get(name)
    question_observation_context.client.sessions.watch(reference.session).wait(
        f"question {name!r} to be resolved",
        lambda snapshot: question_states.is_resolved(snapshot, reference),
        timeout=question_observation_context.wait_policy.feed,
    )
