# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that start, resume, and observe browser sessions."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from pytest_bdd import parsers, then, when

if TYPE_CHECKING:
    from tests.e2e.testkit import browser_contexts, process
    from tests.e2e.testkit.browser import BrowserSessionDriver
    from tests.e2e.testkit.references import Sessions, Shells


@when(parsers.parse('I start browser session "{session_name}" as turn "{turn_name}" with prompt'))
def start_browser_session(
    browser_start_context: browser_contexts.BrowserStartContext,
    session_name: str,
    turn_name: str,
    docstring: str,
) -> None:
    """Start one browser session."""
    started = browser_start_context.driver.start(
        browser_start_context.session_specs.get(session_name), docstring.strip(),
    )
    browser_start_context.sessions.bind(session_name, started.session)
    browser_start_context.turns.bind(turn_name, started.turn)


@when(parsers.parse('I resume browser session "{session_name}" as turn "{turn_name}" with prompt'))
def resume_browser_session(
    browser_resume_context: browser_contexts.BrowserResumeContext,
    session_name: str,
    turn_name: str,
    docstring: str,
) -> None:
    """Resume one browser session."""
    resumed = browser_resume_context.driver.resume(browser_resume_context.sessions.get(session_name), docstring.strip())
    browser_resume_context.sessions.replace(session_name, resumed.session)
    browser_resume_context.continuations.bind(session_name, resumed.continuation)
    browser_resume_context.turns.bind(turn_name, resumed.turn)


@when(parsers.parse('I reload browser session "{session_name}"'))
def reload_browser_session(
    browser_session_driver: BrowserSessionDriver,
    sessions: Sessions,
    session_name: str,
) -> None:
    """Reload one browser session."""
    browser_session_driver.reload(sessions.get(session_name))


@then(
    parsers.parse(
        'the browser running operation time for command "{command_name}" is at least {seconds:d} seconds',
    ),
)
def browser_running_operation_time_is_old_enough(
    browser_session_driver: BrowserSessionDriver,
    shells: Shells,
    command_name: str,
    seconds: int,
) -> None:
    """Check the running-operation age."""
    browser_session_driver.assert_running_elapsed_at_least(shells.get(command_name), seconds)


@then(
    parsers.parse('the browser completed operation time for command "{command_name}" is at least {seconds:d} seconds'),
)
def browser_completed_operation_time_is_old(
    browser_session_driver: BrowserSessionDriver,
    shells: Shells,
    command_name: str,
    seconds: int,
) -> None:
    """Check the completed-operation age."""
    browser_session_driver.assert_completed_elapsed_at_least(shells.get(command_name), seconds)


@when(parsers.parse('I reproduce a rebuild cursor overtake for session "{session_name}"'))
def reproduce_rebuild_cursor_overtake(
    application_process: process.ApplicationProcess,
    sessions: Sessions,
    session_name: str,
) -> None:
    """Reproduce the cursor boundary from a rebuild race."""
    session = sessions.get(session_name)
    path = application_process.config.data_directory / "main.db"
    with sqlite3.connect(path) as connection:
        found = connection.execute(
            "SELECT MAX(value) FROM ("
            "SELECT COALESCE(MAX(cursor), 0) AS value FROM canonical_events "
            "UNION ALL SELECT COALESCE(MAX(cursor), 0) FROM session_entries "
            "UNION ALL SELECT COALESCE(MAX(revision), 0) FROM session_data "
            "UNION ALL SELECT COALESCE(MAX(revision), 0) FROM session_data_actors)",
        ).fetchone()
        boundary = int(found[0]) + 1_000
        connection.execute("UPDATE sqlite_sequence SET seq=? WHERE name='canonical_events'", (boundary,))
        connection.execute("UPDATE session_data SET revision=? WHERE session_id=?", (boundary, str(session.session_id)))
