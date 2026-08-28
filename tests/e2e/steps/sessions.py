"""Session actions and single-fact checks."""

from __future__ import annotations

import pytest
from pytest_bdd import given, parsers, then, when

from api.sessiondata.models.entry import MessageBodyResponse, TurnFinishedBodyResponse
from sdk.client import ActionReceipt, BaqylauClient
from tests.e2e.testkit import selectors
from tests.e2e.testkit.launching import start_named_session
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.repository import RepositoryWorkspace
from tests.e2e.testkit.resume import assert_one_live_session
from tests.e2e.testkit.references import (
    SessionContinuationRef,
    SessionContinuations,
    SessionSpec,
    SessionSpecs,
    Sessions,
    Controls,
    TurnRef,
    Turns,
)
from tests.e2e.testkit import turns as turn_checks


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


@given(parsers.parse(
    'session configuration "{name}" uses {harness} with model {model} and '
    '{effort} effort in a versioned workspace'
))
def configure_session_in_versioned_workspace(
    session_specs: SessionSpecs,
    pytestconfig: pytest.Config,
    versioned_workspace: str,
    name: str,
    harness: str,
    model: str,
    effort: str,
) -> None:
    effective_model = str(pytestconfig.getoption("--e2e-model") or model)
    effective_effort = str(pytestconfig.getoption("--e2e-effort") or effort)
    session_specs.bind(
        name,
        SessionSpec(harness, effective_model, effective_effort, versioned_workspace),
    )


@given(parsers.parse(
    'session configuration "{name}" uses {harness} with model {model} and '
    '{effort} effort in the isolated repository workspace'
))
def configure_session_in_repository_workspace(
    session_specs: SessionSpecs,
    repository_workspace: RepositoryWorkspace,
    pytestconfig: pytest.Config,
    name: str,
    harness: str,
    model: str,
    effort: str,
) -> None:
    effective_model = str(pytestconfig.getoption("--e2e-model") or model)
    effective_effort = str(pytestconfig.getoption("--e2e-effort") or effort)
    session_specs.bind(
        name,
        SessionSpec(
            harness,
            effective_model,
            effective_effort,
            repository_workspace.working_directory,
        ),
    )


@given(parsers.parse(
    'session configuration "{name}" uses {harness} with model {model} and '
    '{effort} effort in the isolated repository root'
))
def configure_session_in_repository_root(
    session_specs: SessionSpecs,
    repository_workspace: RepositoryWorkspace,
    pytestconfig: pytest.Config,
    name: str,
    harness: str,
    model: str,
    effort: str,
) -> None:
    effective_model = str(pytestconfig.getoption("--e2e-model") or model)
    effective_effort = str(pytestconfig.getoption("--e2e-effort") or effort)
    session_specs.bind(
        name,
        SessionSpec(
            harness,
            effective_model,
            effective_effort,
            repository_workspace.repository_root,
        ),
    )


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


def _send_prompt(
    client: BaqylauClient,
    sessions: Sessions,
    turns: Turns,
    session_name: str,
    turn_name: str,
    prompt: str,
) -> ActionReceipt:
    session = sessions.get(session_name)
    before = client.sessions.snapshot(session)
    lead = before.lead()
    expected = lead.statistics.prompt_count + 1
    receipt = client.sessions.send(session, prompt)
    if receipt.status_code != 200 or receipt.outcome.status not in ("sent", "queued"):
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
    return receipt


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
    _send_prompt(
        client,
        sessions,
        turns,
        session_name,
        turn_name,
        docstring.strip(),
    )


@when(parsers.parse(
    'I send prompt to session "{session_name}" as turn "{turn_name}" '
    'and control "{control_name}"'
))
def send_prompt_as_control(
    client: BaqylauClient,
    sessions: Sessions,
    turns: Turns,
    controls: Controls,
    session_name: str,
    turn_name: str,
    control_name: str,
    docstring: str,
) -> None:
    controls.bind(
        control_name,
        _send_prompt(
            client,
            sessions,
            turns,
            session_name,
            turn_name,
            docstring.strip(),
        ),
    )


@when(parsers.parse(
    'I revise the restored draft in session "{session_name}" as turn "{turn_name}"'
))
def revise_restored_draft(
    client: BaqylauClient,
    sessions: Sessions,
    session_continuations: SessionContinuations,
    turns: Turns,
    wait_policy: WaitPolicy,
    session_name: str,
    turn_name: str,
    docstring: str,
) -> None:
    prompt = docstring.strip()
    source = sessions.get(session_name)
    receipt = client.sessions.send(
        source,
        prompt,
        replace_terminal_draft=True,
    )
    if receipt.status_code != 200 or receipt.outcome.status not in ("sent", "queued"):
        raise AssertionError(
            f"draft revision {receipt.request_id!r} was not accepted: {receipt.outcome}"
        )
    owner = client.sessions.wait_for_prompt_owner(
        source,
        prompt=prompt,
        after_cursor=receipt.cursor_before,
        timeout=wait_policy.feed,
    )
    snapshot = client.sessions.snapshot(owner)
    lead = snapshot.lead()
    sessions.replace(session_name, owner)
    session_continuations.bind(
        session_name,
        SessionContinuationRef(before=source, after=owner),
    )
    turns.bind(
        turn_name,
        TurnRef(
            owner,
            prompt,
            receipt.cursor_before,
            max(1, lead.statistics.prompt_count),
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
    current = turn_checks.wait_until_complete(
        client,
        turns.get(name),
        name=name,
        timeout=wait_policy.turn,
    )
    turns.replace(name, current)


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
    answers = turn_checks.final_answer_texts(client, reference)
    found = [
        answer for answer in answers if turn_checks.matches_final_answer(answer, text)
    ]
    assert len(found) == 1, (
        f"turn {name!r} has {len(found)} final answers equal to {text!r}; "
        f"actual final answers: {answers}"
    )


@then(parsers.parse('turn "{name}" has one final answer containing \'{text}\''))
def turn_has_one_final_answer_containing(
    client: BaqylauClient,
    turns: Turns,
    name: str,
    text: str,
) -> None:
    answers = turn_checks.final_answer_texts(client, turns.get(name))
    assert len(answers) == 1 and text in answers[0], (
        f"turn {name!r} does not have one final answer containing {text!r}; "
        f"actual final answers: {answers}"
    )


@then(parsers.parse(
    'turn "{later_name}" starts after turn "{earlier_name}" completes'
))
def turn_starts_after_turn_completes(
    client: BaqylauClient,
    turns: Turns,
    wait_policy: WaitPolicy,
    later_name: str,
    earlier_name: str,
) -> None:
    earlier = turn_checks.resolved(
        client,
        turns.get(earlier_name),
        timeout=wait_policy.turn,
    )
    later = turn_checks.resolved(
        client,
        turns.get(later_name),
        timeout=wait_policy.turn,
    )
    if earlier.turn_id is None or later.activity_cursor is None:
        raise AssertionError("turn order requires resolved turn identities")
    snapshot = client.sessions.snapshot(earlier.session)
    finished = [
        entry
        for entry in snapshot.entries
        if entry.actor_id == earlier.actor_id
        and entry.turn_id == earlier.turn_id
        and isinstance(entry.body, TurnFinishedBodyResponse)
    ]
    assert len(finished) == 1, (
        f"turn {earlier_name!r} has {len(finished)} completion facts"
    )
    assert finished[0].cursor < later.activity_cursor, (
        f"turn {later_name!r} started at {later.activity_cursor} before "
        f"turn {earlier_name!r} completed at {finished[0].cursor}"
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


@then(parsers.parse('session "{name}" has a non-empty native title'))
def session_has_non_empty_native_title(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    client.sessions.watch(sessions.get(name)).wait(
        f"session {name!r} to have a non-empty native title",
        lambda snapshot: (
            True if (snapshot.data.session.title or "").strip() else None
        ),
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


@then(parsers.parse('session "{name}" and all its actors finish'))
def session_and_all_actors_finish(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    snapshot = client.sessions.wait_until_finished(
        sessions.get(name),
        wait_policy.cleanup,
    )
    assert snapshot.data.session.state == "finished"
    assert snapshot.data.actors
    assert all(actor.state == "finished" for actor in snapshot.data.actors)


@then(parsers.parse('session "{name}" is live'))
def session_is_live(client: BaqylauClient, sessions: Sessions, name: str) -> None:
    snapshot = client.sessions.snapshot(sessions.get(name))
    assert snapshot.data.live


@then(parsers.parse('session "{name}" is not live'))
def session_is_not_live(client: BaqylauClient, sessions: Sessions, name: str) -> None:
    snapshot = client.sessions.snapshot(sessions.get(name))
    assert not snapshot.data.live


@then(parsers.parse('session "{name}" keeps one live terminal after revision'))
def session_keeps_one_live_terminal_after_revision(
    client: BaqylauClient,
    session_continuations: SessionContinuations,
    name: str,
) -> None:
    continuation = session_continuations.get(name)
    assert_one_live_session(client, continuation)


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
