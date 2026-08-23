"""Session actions and single-fact checks."""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, then, when

from api.sessiondata.models.entry import EntryResponse, MessageBodyResponse
from sdk.client import BaqylauClient
from sdk.state import SessionSnapshot
from tests.e2e.testkit import selectors
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import SessionSpec, SessionSpecs, Sessions, TurnRef, Turns

TURN_ENDED = ("awaiting_response", "awaiting_background")


def _turn_enders(snapshot: SessionSnapshot, reference: TurnRef) -> list[EntryResponse]:
    assert reference.prompt_cursor is not None
    later_prompts = [
        entry.cursor
        for entry in snapshot.entries
        if entry.cursor > reference.prompt_cursor
        and isinstance(entry.body, MessageBodyResponse)
        and entry.body.role == "user"
        and entry.body.phase == "prompt"
    ]
    boundary = min(later_prompts) if later_prompts else None
    return [
        entry
        for entry in snapshot.messages(
            actor_id=snapshot.lead().actor_id,
            role="assistant",
            phase="end_turn",
        )
        if entry.cursor > reference.prompt_cursor
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
    spec = session_specs.get(session_name)
    launch = client.sessions.launch(
        spec.harness,
        workspace=workspace,
        prompt=prompt,
        model=spec.model,
        effort=spec.effort,
    )
    session = client.sessions.wait_for_session(launch, wait_policy.session_announcement)
    sessions.bind(session_name, session)
    turns.bind(turn_name, TurnRef(session, prompt, 0, 1))


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
    expected = sum(actor.statistics.prompt_count for actor in before.data.actors) + 1
    receipt = client.sessions.send(session, prompt)
    if receipt.status_code not in (200, 202):
        raise AssertionError(
            f"send action {receipt.request_id!r} was not accepted: {receipt.outcome}"
        )
    turns.bind(turn_name, TurnRef(session, prompt, receipt.cursor_before, expected))


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
        prompt_count = sum(actor.statistics.prompt_count for actor in snapshot.data.actors)
        quiet = snapshot.lead().status in TURN_ENDED
        return True if len(enders) == 1 and prompt_count >= current.expected_prompt_count and quiet else None

    watch.wait(
        lambda snapshot: (
            f"turn {name!r} to have one end message and a quiet lead; "
            f"lead status is {snapshot.lead().status!r}"
        ),
        completed,
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
