"""Status and color checks shared by dashboard and terminal journeys."""

from __future__ import annotations

from pytest_bdd import given, parsers, then

from api.sessiondata.models.entry import MessageBodyResponse, TurnStartedBodyResponse
from domain.sessiondata import ActorStatus
from sdk.client import BaqylauClient, wait_for
from terminal.theme import tab_appearance
from tests.e2e.testkit.policy import WaitPolicy
from tests.e2e.testkit.repository import RepositoryWorkspace
from tests.e2e.testkit.references import SessionJourneys, Sessions
from tests.e2e.testkit.status_colors import KittyTabColorReader


@given("the isolated repository has a blocking Claude Stop hook")
def install_blocking_stop_hook(
    repository_workspace: RepositoryWorkspace,
) -> None:
    repository_workspace.install_blocking_stop_hook()


@then("the blocking Claude Stop hook starts")
def blocking_stop_hook_starts(
    repository_workspace: RepositoryWorkspace,
    wait_policy: WaitPolicy,
) -> None:
    marker = repository_workspace.blocking_stop_marker
    wait_for(
        "the blocking Claude Stop hook to start",
        lambda: True if marker.exists() else None,
        timeout=wait_policy.turn,
    )


@then(parsers.parse(
    'the blocked Stop hook feedback starts a new turn in session "{name}"'
))
def blocked_stop_feedback_starts_new_turn(
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
    name: str,
) -> None:
    def resumed() -> bool | None:
        snapshot = client.sessions.snapshot(sessions.get(name))
        feedback = [
            entry
            for entry in snapshot.entries
            if isinstance(entry.body, MessageBodyResponse)
            and entry.body.role == "system"
            and entry.body.phase == "synthetic"
            and entry.body.content.text.startswith("Stop hook feedback:")
        ]
        if not feedback:
            return None
        feedback_turn = feedback[-1].turn_id
        starts = [
            entry
            for entry in snapshot.entries
            if isinstance(entry.body, TurnStartedBodyResponse)
            and entry.turn_id == feedback_turn
        ]
        return True if feedback_turn is not None and len(starts) == 1 else None

    wait_for(
        f"session {name!r} Stop hook feedback to start a new turn",
        resumed,
        timeout=wait_policy.turn,
    )


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


@then(parsers.parse(
    'for {seconds:d} seconds the terminal tab for journey session "{name}" '
    'does not have color {status}'
))
def terminal_tab_does_not_have_status_color(
    session_journeys: SessionJourneys,
    terminal_color_reader: KittyTabColorReader,
    name: str,
    status: str,
    seconds: int,
) -> None:
    journey = session_journeys.get(name)
    terminal_color_reader.assert_not_seen_for(
        journey.window_id,
        tab_appearance(ActorStatus(status)),
        seconds,
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
