# Copyright (c) 2026 Zhambyl Yermagambet
"""Test canonical sessiondata status of monitors."""

from __future__ import annotations

from tests import (
    canonical_sessiondata_actor_access as actor_access,
    canonical_sessiondata_fixtures as session_fixtures,
    canonical_sessiondata_folding as folding,
    canonical_sessiondata_values as session_values,
)
from tests.canonical_sessiondata_components import domain as session_domain

MONITOR_SHELL_ID = session_domain.ids.ShellId("m1")


def test_monitors_and_bg_jobs_are_counted_apart() -> None:
    """Verify monitors and background jobs are counted apart."""
    state = folding.fold(
        *session_fixtures.alive(),
        session_domain.event_shell.ShellStarted(
            MONITOR_SHELL_ID,
            session_domain.content.TextContent("watch"),
            session_domain.outcomes.ExecutionMode.MONITOR,
            None,
        ),
        session_domain.event_shell.ShellStarted(
            session_values.BACKGROUND_SHELL_ID,
            session_domain.content.TextContent("tail"),
            session_domain.outcomes.ExecutionMode.BACKGROUND,
            None,
        ),
    )
    background = actor_access.lead_background(state)
    assert (background.monitor_count, background.background_job_count) == (1, 1)
    assert set(background.running_shell_ids) == {
        MONITOR_SHELL_ID,
        session_values.BACKGROUND_SHELL_ID,
    }


def test_monitor_output_after_turn_releases() -> None:
    """Verify a monitor's output end releases the actor like a background job."""
    assert (
        session_fixtures.status_after(
            session_domain.event_shell.ShellStarted(
                MONITOR_SHELL_ID,
                session_domain.content.TextContent("watch"),
                session_domain.outcomes.ExecutionMode.MONITOR,
                None,
            ),
            session_fixtures.succeeded_turn(),
            session_domain.event_shell.ShellOutputFinished(
                MONITOR_SHELL_ID,
                session_domain.outcomes.Outcome.SUCCEEDED,
            ),
        )
        == session_values.AWAITING_RESPONSE_STATE
    )
