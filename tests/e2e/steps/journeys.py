"""Session actions across the dashboard and a real terminal."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from sdk.client import BaqylauClient
from tests.e2e.testkit.journeys import JourneyDriver
from tests.e2e.testkit.resume import assert_saved_metadata
from tests.e2e.testkit.references import (
    JourneyOrigin,
    SessionContinuations,
    SessionJourneys,
    SessionSpecs,
    Sessions,
    Turns,
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
