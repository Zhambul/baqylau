"""Application replacement actions and durable-state checks."""

from __future__ import annotations

from pytest_bdd import parsers, then, when

from sdk.client import BaqylauClient
from tests.e2e.testkit.process import ApplicationProcess
from tests.e2e.testkit.references import (
    ApplicationRestartRef,
    ApplicationRestarts,
    Sessions,
    Turns,
)


@when(parsers.parse('I restart Baqylau as application restart "{name}"'))
def restart_application(
    application_process: ApplicationProcess,
    client: BaqylauClient,
    application_restarts: ApplicationRestarts,
    name: str,
) -> None:
    before, after = application_process.restart()
    client.application.wait_until_ready()
    application_restarts.bind(name, ApplicationRestartRef(before, after))


@then(parsers.parse('application restart "{name}" replaces the server process'))
def application_restart_replaces_process(
    client: BaqylauClient,
    application_restarts: ApplicationRestarts,
    name: str,
) -> None:
    restart = application_restarts.get(name)
    assert restart.after_process_id != restart.before_process_id
    assert client.application.health().process_id == restart.after_process_id


@then(parsers.parse('session "{session_name}" remains live and keeps turn "{turn_name}" after restart'))
def session_remains_live_with_turn(
    client: BaqylauClient,
    sessions: Sessions,
    turns: Turns,
    session_name: str,
    turn_name: str,
) -> None:
    session = sessions.get(session_name)
    turn = turns.get(turn_name)
    snapshot = client.sessions.snapshot(session)
    assert snapshot.data.live
    assert snapshot.data.session.state != "finished"
    assert any(entry.turn_id == turn.turn_id for entry in snapshot.entries)


@then(parsers.parse('session "{session_name}" has no repeated entry identity'))
def session_has_no_repeated_entry_identity(
    client: BaqylauClient,
    sessions: Sessions,
    session_name: str,
) -> None:
    entries = client.sessions.snapshot(sessions.get(session_name)).entries
    identities = [entry.entry_id for entry in entries]
    assert len(identities) == len(set(identities))
