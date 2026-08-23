"""Session actions and single-fact checks."""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, then, when

from api.sessiondata.models.entry import EntryResponse, MessageBodyResponse
from sdk.client import BaqylauClient
from sdk.state import SessionSnapshot
from tests.e2e.testkit import selectors
from tests.e2e.testkit.launching import start_named_session
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import SessionSpec, SessionSpecs, Sessions, TurnRef, Turns


def _turn_enders(snapshot: SessionSnapshot, reference: TurnRef) -> list[EntryResponse]:
    assert reference.prompt_cursor is not None
    answer_after = max(
        reference.prompt_cursor,
        reference.completion_after_cursor or reference.prompt_cursor,
    )
    if reference.actor_id is None:
        raise AssertionError("turn does not have a resolved actor identity")
    boundary = selectors.next_prompt_cursor(snapshot, reference, after=answer_after)
    return [
        entry
        for entry in snapshot.messages(
            actor_id=reference.actor_id,
            role="assistant",
            phase="end_turn",
        )
        if entry.cursor > answer_after
        and (boundary is None or entry.cursor < boundary)
        and isinstance(entry.body, MessageBodyResponse)
        and entry.body.recipient_actor_id is None
    ]


@given(parsers.parse(
    'session configuration "{name}" uses {harness} with model {model} and {effort} effort'
))
def configure_session(
    session_specs: SessionSpecs,
    pytestconfig: pytest.Config,
    name: str,
    harness: str,
    model: str,
    effort: str,
) -> None:
    effective_model = str(pytestconfig.getoption("--e2e-model") or model)
    effective_effort = str(pytestconfig.getoption("--e2e-effort") or effort)
    session_specs.bind(name, SessionSpec(harness, effective_model, effective_effort))


@when(parsers.parse(
    'I launch session "{session_name}" as turn "{turn_name}" with prompt'
))
def launch_session(
    client: BaqylauClient,
    workspace: str,
    session_specs: SessionSpecs,
    sessions: Sessions,
    turns: Turns,
    wait_policy: WaitPolicy,
    session_name: str,
    turn_name: str,
    docstring: str,
) -> None:
    prompt = docstring.strip()
    start_named_session(
        client,
        workspace,
        session_specs,
        sessions,
        turns,
        wait_policy,
        session_name=session_name,
        turn_name=turn_name,
        prompt=prompt,
    )


@when(parsers.parse(
    'I send prompt to session "{session_name}" as turn "{turn_name}"'
))
def send_prompt(
    client: BaqylauClient,
    sessions: Sessions,
    turns: Turns,
    session_name: str,
    turn_name: str,
    docstring: str,
) -> None:
    prompt = docstring.strip()
    session = sessions.get(session_name)
    before = client.sessions.snapshot(session)
    lead = before.lead()
    expected = lead.statistics.prompt_count + 1
    receipt = client.sessions.send(session, prompt)
    if receipt.status_code != 200 or receipt.outcome.status != "acknowledged":
        raise AssertionError(
            f"send action {receipt.request_id!r} was not accepted: {receipt.outcome}"
        )
    turns.bind(
        turn_name,
        TurnRef(
            session,
            prompt,
            receipt.cursor_before,
            expected,
            actor_id=lead.actor_id,
        ),
    )


@then(parsers.parse('turn "{name}" completes'))
def turn_completes(
    client: BaqylauClient,
    turns: Turns,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    current = turns.get(name)
    watch = client.sessions.watch(current.session)
    current = selectors.turn(watch, current, wait_policy.turn)
    turns.replace(name, current)

    def completed(snapshot: SessionSnapshot) -> bool | None:
        enders = _turn_enders(snapshot, current)
        assert current.actor_id is not None
        prompt_count = snapshot.actor(current.actor_id).statistics.prompt_count
        if len(enders) > 1:
            raise AssertionError(f"turn {name!r} has {len(enders)} final answers")
        return (
            True
            if len(enders) == 1 and prompt_count >= current.expected_prompt_count
            else None
        )

    watch.wait(
        f"turn {name!r} to have exactly one final answer and its prompt",
        completed,
        timeout=wait_policy.turn,
    )


@then(parsers.parse('turn "{name}" has state {state}'))
def turn_has_state(
    client: BaqylauClient,
    turns: Turns,
    wait_policy: WaitPolicy,
    name: str,
    state: str,
) -> None:
    current = turns.get(name)
    watch = client.sessions.watch(current.session)
    current = selectors.turn(watch, current, wait_policy.turn)
    turns.replace(name, current)
    assert current.turn_id is not None
    watch.wait(
        f"turn {name!r} to have state {state!r}",
        lambda snapshot: True if snapshot.turn_state(current.turn_id or "") == state else None,
        timeout=wait_policy.turn,
    )


@then(parsers.parse('turn "{name}" has prompt \'{text}\''))
def turn_has_prompt(client: BaqylauClient, turns: Turns, name: str, text: str) -> None:
    reference = turns.get(name)
    snapshot = client.sessions.snapshot(reference.session)
    found = [
        entry
        for entry in snapshot.entries
        if entry.turn_id == reference.turn_id
        and isinstance(entry.body, MessageBodyResponse)
        and entry.body.role == "user"
        and entry.body.phase == "prompt"
        and entry.body.content.text.strip() == text
    ]
    assert len(found) == 1, f"turn {name!r} has {len(found)} matching prompts"


@then(parsers.parse('turn "{name}" has final answer \'{text}\''))
def turn_has_final_answer(client: BaqylauClient, turns: Turns, name: str, text: str) -> None:
    reference = turns.get(name)
    snapshot = client.sessions.snapshot(reference.session)
    answers = [
        entry.body.content.text.strip()
        for entry in _turn_enders(snapshot, reference)
        if isinstance(entry.body, MessageBodyResponse)
    ]
    found = [
        answer for answer in answers if answer == text
    ]
    assert len(found) == 1, (
        f"turn {name!r} has {len(found)} final answers equal to {text!r}; "
        f"actual final answers: {answers}"
    )


@then(parsers.parse('session "{name}" reports its configured model'))
def session_reports_model(
    client: BaqylauClient,
    sessions: Sessions,
    session_specs: SessionSpecs,
    name: str,
) -> None:
    wanted = session_specs.get(name).model.casefold()
    reported = (client.sessions.snapshot(sessions.get(name)).lead().model or "").casefold()
    assert wanted in reported, f"configured model {wanted!r}, reported model {reported!r}"


@then(parsers.parse('session "{name}" reports model {model}'))
def session_reports_selected_model(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
    model: str,
) -> None:
    wanted = model.casefold()
    client.sessions.watch(sessions.get(name)).wait(
        f"session {name!r} to report model {model!r}",
        lambda snapshot: (
            True if wanted in (snapshot.lead().model or "").casefold() else None
        ),
        timeout=wait_policy.feed,
    )


@then(parsers.parse('session "{name}" reports its configured effort'))
def session_reports_effort(
    client: BaqylauClient,
    sessions: Sessions,
    session_specs: SessionSpecs,
    name: str,
) -> None:
    wanted = session_specs.get(name).effort
    reported = client.sessions.snapshot(sessions.get(name)).lead().effort
    assert reported == wanted, f"configured effort {wanted!r}, reported effort {reported!r}"


@then(parsers.parse('session "{name}" has title \'{title}\''))
def session_has_title(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
    title: str,
) -> None:
    session = sessions.get(name)
    client.sessions.watch(session).wait(
        f"session {name!r} to have title {title!r}",
        lambda snapshot: True if snapshot.data.session.title == title else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('session "{name}" reports effort {effort}'))
def session_reports_exact_effort(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
    effort: str,
) -> None:
    session = sessions.get(name)
    client.sessions.watch(session).wait(
        f"session {name!r} to report effort {effort!r}",
        lambda snapshot: True if snapshot.lead().effort == effort else None,
        timeout=wait_policy.feed,
    )


@then(parsers.parse('session "{name}" finishes'))
def session_finishes(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    client.sessions.wait_until_finished(sessions.get(name), wait_policy.cleanup)


@then(parsers.parse('session "{name}" is live'))
def session_is_live(client: BaqylauClient, sessions: Sessions, name: str) -> None:
    snapshot = client.sessions.snapshot(sessions.get(name))
    assert snapshot.data.live


@then(parsers.parse('session "{name}" has repository status'))
def session_has_repository_status(
    client: BaqylauClient,
    sessions: Sessions,
    name: str,
) -> None:
    snapshot = client.sessions.snapshot(sessions.get(name))
    assert snapshot.data.repository is not None
    assert snapshot.data.repository.branch


@then(parsers.parse('session "{name}" title is not \'{title}\''))
def session_title_is_not(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
    title: str,
) -> None:
    client.sessions.watch(sessions.get(name)).wait(
        f"session {name!r} title to change from {title!r}",
        lambda snapshot: (
            True
            if snapshot.data.session.title
            and snapshot.data.session.title != title
            else None
        ),
        timeout=wait_policy.feed,
    )
