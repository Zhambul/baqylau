"""Status and color checks shared by dashboard and terminal journeys."""

from __future__ import annotations

from pytest_bdd import parsers, then

from domain.sessiondata import ActorStatus
from sdk.client import BaqylauClient, wait_for
from terminal.theme import tab_appearance
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.references import SessionJourneys, Sessions
from tests.e2e.testkit.status_colors import KittyTabColorReader


@then(parsers.parse(
    'the terminal tab for journey session "{name}" has color {status}'
))
def terminal_tab_has_status_color(
    session_journeys: SessionJourneys,
    terminal_color_reader: KittyTabColorReader,
    wait_policy: WaitPolicy,
    name: str,
    status: str,
) -> None:
    journey = session_journeys.get(name)
    terminal_color_reader.wait_for(
        journey.window_id,
        tab_appearance(ActorStatus(status)),
        wait_policy.feed,
    )


@then(parsers.parse('the lead in session "{name}" has status {status}'))
def session_lead_has_status(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
    status: str,
) -> None:
    expected = ActorStatus(status)
    wait_for(
        f"session {name!r} lead to have status {status!r}",
        lambda: (
            True
            if client.sessions.snapshot(sessions.get(name)).lead().status == expected
            else None
        ),
        timeout=wait_policy.feed,
    )
