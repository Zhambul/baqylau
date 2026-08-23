"""Session control actions and control outcome checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from api.controls.models.control_outcome_response import RewindResultResponse
from api.sessiondata.models.entry import MessageBodyResponse
from sdk.client import BaqylauClient
from tests.e2e.testkit.references import Controls, Sessions, Turns


@when(parsers.parse(
    'I request backgrounding in session "{session_name}" as control "{control_name}"'
))
def request_backgrounding(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    control_name: str,
) -> None:
    controls.bind(control_name, client.sessions.background(sessions.get(session_name)))


@when(parsers.parse(
    'I request interruption in session "{session_name}" as control "{control_name}"'
))
def request_interruption(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    control_name: str,
) -> None:
    controls.bind(control_name, client.sessions.interrupt(sessions.get(session_name)))


@when(parsers.parse(
    'I rename session "{session_name}" to \'{new_name}\' as control "{control_name}"'
))
def rename_session(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    new_name: str,
    control_name: str,
) -> None:
    controls.bind(
        control_name,
        client.sessions.rename(sessions.get(session_name), new_name),
    )


@when(parsers.parse(
    'I request an automatic name for session "{session_name}" as control "{control_name}"'
))
def auto_name_session(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    control_name: str,
) -> None:
    controls.bind(control_name, client.sessions.auto_name(sessions.get(session_name)))


@when(parsers.parse(
    'I select model {model} in session "{session_name}" as control "{control_name}"'
))
def select_model(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    model: str,
    control_name: str,
) -> None:
    controls.bind(
        control_name,
        client.sessions.select_model(sessions.get(session_name), model),
    )


@when(parsers.parse(
    'I select {effort} effort in session "{session_name}" as control "{control_name}"'
))
def select_effort(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    effort: str,
    control_name: str,
) -> None:
    controls.bind(
        control_name,
        client.sessions.select_effort(sessions.get(session_name), effort),
    )


@when(parsers.parse(
    'I request compaction in session "{session_name}" as control "{control_name}"'
))
def request_compaction(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    control_name: str,
) -> None:
    controls.bind(control_name, client.sessions.compact(sessions.get(session_name)))


@when(parsers.parse(
    'I rewind session "{session_name}" to turn "{turn_name}" with {mode} mode '
    'as control "{control_name}"'
))
def apply_rewind(
    client: BaqylauClient,
    sessions: Sessions,
    turns: Turns,
    controls: Controls,
    session_name: str,
    turn_name: str,
    mode: str,
    control_name: str,
) -> None:
    session = sessions.get(session_name)
    target = turns.get(turn_name)
    if target.session != session:
        raise AssertionError(f"turn {turn_name!r} does not belong to session {session_name!r}")
    if (
        target.actor_id is None
        or target.prompt_cursor is None
        or target.prompt_message_id is None
    ):
        raise AssertionError(f"turn {turn_name!r} does not have a resolved prompt identity")
    snapshot = client.sessions.snapshot(session)
    newer_prompt_count = sum(
        1
        for entry in snapshot.entries
        if entry.cursor > target.prompt_cursor
        and entry.actor_id == target.actor_id
        and isinstance(entry.body, MessageBodyResponse)
        and entry.body.role == "user"
        and entry.body.phase == "prompt"
    )
    controls.bind(
        control_name,
        client.sessions.apply_rewind(
            session,
            target_message_id=target.prompt_message_id,
            target_text=target.prompt,
            newer_prompt_count=newer_prompt_count,
            mode=mode,
        ),
    )


@when(parsers.parse(
    'I close session "{session_name}" as control "{control_name}"'
))
def close_session(
    client: BaqylauClient,
    sessions: Sessions,
    controls: Controls,
    session_name: str,
    control_name: str,
) -> None:
    controls.bind(control_name, client.sessions.close(sessions.get(session_name)))


@then(parsers.parse('control "{name}" response is accepted'))
def control_response_is_accepted(controls: Controls, name: str) -> None:
    receipt = controls.get(name)
    assert receipt.status_code == 200, f"control {name!r} returned {receipt.outcome}"


@then(parsers.parse('control "{name}" response is rejected'))
def control_response_is_rejected(controls: Controls, name: str) -> None:
    receipt = controls.get(name)
    assert receipt.status_code == 409, f"control {name!r} returned {receipt.outcome}"


@then(parsers.parse('control "{name}" outcome is acknowledged'))
def control_outcome_is_acknowledged(controls: Controls, name: str) -> None:
    receipt = controls.get(name)
    assert receipt.outcome.status == "acknowledged"


@then(parsers.parse('control "{name}" outcome is rejected'))
def control_outcome_is_rejected(controls: Controls, name: str) -> None:
    receipt = controls.get(name)
    assert receipt.outcome.status == "rejected"


@then(parsers.parse('control "{control_name}" restores turn "{turn_name}"'))
def control_restores_turn(
    controls: Controls,
    turns: Turns,
    control_name: str,
    turn_name: str,
) -> None:
    outcome = controls.get(control_name).outcome
    assert isinstance(outcome, RewindResultResponse)
    assert outcome.restored_text == turns.get(turn_name).prompt
