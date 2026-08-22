"""Upgrade coverage for durable documents served by ``/sessionData``."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from pytest_bdd import then, when

from impl.world import World
from support import observe
from support.daemon import Daemon, start

REPOSITORY_ROOT = str(Path(__file__).resolve().parents[3])


@when("the dashboard restarts with that actor model stored in the previous format")
def _restart_with_previous_actor_model(world: World, daemon: Daemon) -> None:
    """Recreate the version-4 row which broke the real long-lived database.

    Stopping and starting the real daemon is essential: schema migrations run
    at process initialization, which an in-process repository test cannot prove.
    """
    assert world.session_id is not None
    daemon.stop()
    with sqlite3.connect(daemon.main_database_path) as connection:
        changed = connection.execute(
            """UPDATE session_data_actors
               SET payload = json_set(
                   json_remove(payload, '$.model.name'),
                   '$.model.native_id', json_extract(payload, '$.model.name'),
                   '$.model.selection_id', 'legacy-selection'
               )
               WHERE session_id = ?
                 AND json_type(payload, '$.model.name') = 'text'""",
            (str(world.session_id),),
        ).rowcount
        assert changed >= 1, "the session had no persisted actor model to downgrade"
        connection.execute("UPDATE schema_version SET version = 4 WHERE id = 1")

    restarted = start(REPOSITORY_ROOT, daemon.data_directory, daemon.port)
    daemon.log_path = restarted.log_path
    daemon.process = restarted.process


@then("the session list still includes that session")
def _session_list_still_includes_it(world: World, daemon: Daemon) -> None:
    assert world.session_id is not None
    assert str(world.session_id) in observe.session_ids(daemon)
