"""Session actions across the dashboard and a real terminal."""

from __future__ import annotations

import time

from pytest_bdd import parsers, then, when

from sdk.client import BaqylauClient, wait_for
from tests.e2e.testkit.journeys import JourneyDriver
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.resume import assert_saved_metadata
from tests.e2e.testkit.references import (
    JourneyOrigin,
    SessionContinuations,
    SessionJourneys,
    SessionSpecs,
    Sessions,
    Turns,
    TurnRef,
)


def _origin(value: str) -> JourneyOrigin:
    try:
        return JourneyOrigin(value)
    except ValueError as error:
        raise AssertionError(f"unknown journey origin {value!r}") from error


@when(parsers.parse('I start journey session "{session_name}" from the {origin} as turn "{turn_name}" with prompt'))
def start_journey_session(
    journey_driver: JourneyDriver,
    session_specs: SessionSpecs,
    session_journeys: SessionJourneys,
    sessions: Sessions,
    turns: Turns,
    session_name: str,
    origin: str,
    turn_name: str,
    docstring: str,
) -> None:
    started = journey_driver.start(
        session_specs.get(session_name),
        _origin(origin),
        docstring.strip(),
    )
    session_journeys.bind(session_name, started.journey)
    sessions.bind(session_name, started.journey.session)
    turns.bind(turn_name, started.turn)


@when(parsers.parse('I continue journey session "{session_name}" from the {origin} as turn "{turn_name}" with prompt'))
def continue_journey_session(
    journey_driver: JourneyDriver,
    session_journeys: SessionJourneys,
    turns: Turns,
    session_name: str,
    origin: str,
    turn_name: str,
    docstring: str,
) -> None:
    continued = journey_driver.continue_session(
        session_journeys.get(session_name),
        _origin(origin),
        docstring.strip(),
    )
    session_journeys.replace(session_name, continued.journey)
    turns.bind(turn_name, continued.turn)


@when(parsers.parse('I close the terminal for journey session "{session_name}"'))
def close_journey_terminal(
    journey_driver: JourneyDriver,
    session_journeys: SessionJourneys,
    session_name: str,
) -> None:
    journey_driver.stop_terminal(session_journeys.get(session_name))


@when(parsers.parse("I submit native command '{command}' to journey session \"{session_name}\""))
def submit_native_journey_command(
    journey_driver: JourneyDriver,
    session_journeys: SessionJourneys,
    session_name: str,
    command: str,
) -> None:
    journey_driver.submit_native_command(
        session_journeys.get(session_name),
        command,
    )


@when(
    parsers.parse(
        'I insert terminal draft \'{text}\' in journey session "{session_name}"'
    )
)
def insert_journey_terminal_draft(
    journey_driver: JourneyDriver,
    session_journeys: SessionJourneys,
    session_name: str,
    text: str,
) -> None:
    journey_driver.insert_terminal_draft(
        session_journeys.get(session_name),
        text,
    )


@when(
    parsers.parse(
        'I put journey session "{session_name}" in {mode} editor mode'
    )
)
def set_journey_editor_mode(
    journey_driver: JourneyDriver,
    session_journeys: SessionJourneys,
    session_name: str,
    mode: str,
) -> None:
    if mode == "standard":
        return
    if mode != "visual":
        raise AssertionError(f"unknown editor mode {mode!r}")
    journey_driver.use_visual_editor_mode(session_journeys.get(session_name))


@then(
    parsers.re(
        r'journey session "(?P<session_name>[^"]+)" terminal draft is exactly '
        r"'(?P<text>.*)'"
    )
)
def journey_terminal_draft_is_exact(
    client: BaqylauClient,
    session_journeys: SessionJourneys,
    wait_policy: WaitPolicy,
    session_name: str,
    text: str,
) -> None:
    journey = session_journeys.get(session_name)

    def observed() -> bool | None:
        input_state = client.preferences.session_state(journey.session).terminal.input_state
        return True if input_state is not None and input_state.typed_text == text else None

    wait_for(
        f"journey session {session_name!r} terminal draft to equal {text!r}",
        observed,
        timeout=wait_policy.feed,
    )


@when(
    parsers.parse(
        'I send the shared draft for journey session "{session_name}" as turn "{turn_name}"'
    )
)
def send_journey_shared_draft(
    client: BaqylauClient,
    session_journeys: SessionJourneys,
    turns: Turns,
    session_name: str,
    turn_name: str,
) -> None:
    journey = session_journeys.get(session_name)
    application = client.preferences.session_state(journey.session)
    draft = application.composer.draft
    if draft is None or not draft.text:
        raise AssertionError(f"journey session {session_name!r} has no shared draft")
    before = client.sessions.snapshot(journey.session)
    lead = before.lead()
    client.preferences.save_composer_draft(
        journey.session,
        text="",
        origin="e2e-browser",
        sequence=time.time() * 1000,
    )
    receipt = client.sessions.send(journey.session, draft.text)
    if receipt.status_code != 200 or receipt.outcome.status not in ("sent", "queued"):
        raise AssertionError(f"shared draft was not accepted: {receipt.outcome}")
    turns.bind(
        turn_name,
        TurnRef(
            journey.session,
            draft.text,
            receipt.cursor_before,
            lead.statistics.prompt_count + 1,
            actor_id=lead.actor_id,
        ),
    )


@when(parsers.parse('I interrupt journey session "{session_name}" from its terminal'))
def interrupt_journey_session_from_terminal(
    journey_driver: JourneyDriver,
    session_journeys: SessionJourneys,
    session_name: str,
) -> None:
    journey_driver.interrupt_from_terminal(session_journeys.get(session_name))


@when(
    parsers.parse(
        'I start journey session "{new_name}" with native /new in journey session '
        '"{old_name}" as turn "{turn_name}" with prompt'
    )
)
def start_new_native_journey_session(
    journey_driver: JourneyDriver,
    session_journeys: SessionJourneys,
    sessions: Sessions,
    turns: Turns,
    new_name: str,
    old_name: str,
    turn_name: str,
    docstring: str,
) -> None:
    started = journey_driver.start_new_native_session(
        session_journeys.get(old_name),
        docstring.strip(),
    )
    session_journeys.bind(new_name, started.journey)
    sessions.bind(new_name, started.journey.session)
    turns.bind(turn_name, started.turn)


@then(parsers.parse('journey session "{new_name}" reuses the terminal from journey session "{old_name}"'))
def journey_session_reuses_terminal(
    session_journeys: SessionJourneys,
    new_name: str,
    old_name: str,
) -> None:
    new = session_journeys.get(new_name)
    old = session_journeys.get(old_name)
    assert new.session != old.session
    assert new.window_id == old.window_id


@when(
    parsers.parse(
        'I run unattended session "{detached_name}" with the terminal environment '
        'from journey session "{host_name}" and prompt'
    )
)
def run_unattended_session_with_host_environment(
    journey_driver: JourneyDriver,
    session_specs: SessionSpecs,
    session_journeys: SessionJourneys,
    sessions: Sessions,
    detached_name: str,
    host_name: str,
    docstring: str,
) -> None:
    sessions.bind(
        detached_name,
        journey_driver.run_unattended_with_inherited_window(
            session_specs.get(detached_name),
            session_journeys.get(host_name),
            docstring.strip(),
        ),
    )


@when(parsers.parse('I resume journey session "{session_name}" from the {origin} as turn "{turn_name}" with prompt'))
def resume_journey_session(
    journey_driver: JourneyDriver,
    session_journeys: SessionJourneys,
    session_continuations: SessionContinuations,
    sessions: Sessions,
    turns: Turns,
    session_name: str,
    origin: str,
    turn_name: str,
    docstring: str,
) -> None:
    resumed = journey_driver.resume(
        session_journeys.get(session_name),
        _origin(origin),
        docstring.strip(),
    )
    session_journeys.replace(session_name, resumed.journey)
    session_continuations.bind(session_name, resumed.continuation)
    sessions.replace(session_name, resumed.journey.session)
    turns.bind(turn_name, resumed.turn)


@then(parsers.parse('journey session "{session_name}" uses its exact saved resume metadata'))
def journey_uses_saved_resume_metadata(
    client: BaqylauClient,
    session_continuations: SessionContinuations,
    session_name: str,
) -> None:
    continuation = session_continuations.get(session_name)
    assert_saved_metadata(client, continuation)


@then(parsers.parse('journey session "{session_name}" has one live terminal and one logical lineage'))
def journey_has_one_terminal_and_lineage(
    client: BaqylauClient,
    session_journeys: SessionJourneys,
    session_continuations: SessionContinuations,
    session_name: str,
) -> None:
    journey = session_journeys.get(session_name)
    continuation = session_continuations.get(session_name)
    current = client.sessions.snapshot(journey.session)
    if continuation.before != continuation.after:
        assert current.data.session.continued_from == continuation.before.session_id
    terminal = client.preferences.session_state(journey.session).terminal
    assert terminal.window_id == journey.window_id
    live = [
        item.session.session_id
        for item in client.sessions.list().sessions
        if item.live and item.session.working_directory == current.data.session.working_directory
    ]
    assert live == [journey.session.session_id]
