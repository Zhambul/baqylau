# Copyright (c) 2026 Zhambyl Yermagambet
"""Test canonical sessiondata status background."""

from __future__ import annotations

from tests import (
    canonical_sessiondata_actor_access as actor_access,
    canonical_sessiondata_fixtures as session_fixtures,
    canonical_sessiondata_folding as folding,
    canonical_sessiondata_values as session_values,
)
from tests.canonical_sessiondata_components import domain as session_domain


def test_bg_job_ends_on_its_own_notice_not_on_its() -> None:
    """Verify a background job ends on its own notification not on its launch."""
    assert (
        session_fixtures.status_after(
            session_domain.event_shell.ShellStarted(
                session_values.BACKGROUND_SHELL_ID,
                session_domain.content.TextContent("tail -f log"),
                session_domain.outcomes.ExecutionMode.BACKGROUND,
                None,
            ),
            session_domain.event_shell.ShellFinished(
                session_values.BACKGROUND_SHELL_ID,
                session_domain.outcomes.Outcome.SUCCEEDED,
                None,
                None,
            ),
            session_domain.event_shell.ShellOutputFinished(
                session_values.BACKGROUND_SHELL_ID,
                session_domain.outcomes.Outcome.SUCCEEDED,
            ),
            session_fixtures.succeeded_turn(),
        )
        == session_values.AWAITING_RESPONSE_STATE
    )


def test_bg_output_that_ends_after_turn_releases() -> None:
    """Verify background output that ends after the turn releases the actor."""
    assert (
        session_fixtures.status_after(
            session_domain.event_shell.ShellStarted(
                session_values.BACKGROUND_SHELL_ID,
                session_domain.content.TextContent("tail -f log"),
                session_domain.outcomes.ExecutionMode.BACKGROUND,
                None,
            ),
            session_domain.event_shell.ShellFinished(
                session_values.BACKGROUND_SHELL_ID,
                session_domain.outcomes.Outcome.SUCCEEDED,
                None,
                None,
            ),
            session_fixtures.succeeded_turn(),
            session_domain.event_shell.ShellOutputFinished(
                session_values.BACKGROUND_SHELL_ID,
                session_domain.outcomes.Outcome.SUCCEEDED,
            ),
        )
        == session_values.AWAITING_RESPONSE_STATE
    )


def test_rejected_bg_job_ends_on_failed_launch() -> None:
    """Remove a background job when its launch fails."""
    state = folding.fold(
        *session_fixtures.alive(),
        session_domain.event_shell.ShellStarted(
            session_values.BACKGROUND_SHELL_ID,
            session_domain.content.TextContent("blocked command"),
            session_domain.outcomes.ExecutionMode.BACKGROUND,
            None,
        ),
        session_domain.event_shell.ShellFinished(
            session_values.BACKGROUND_SHELL_ID,
            session_domain.outcomes.Outcome.FAILED,
            None,
            None,
        ),
        session_fixtures.succeeded_turn(),
    )
    assert not actor_access.lead_background(state).running_shell_ids
    assert actor_access.lead_status(state) == session_values.AWAITING_RESPONSE_STATE


def test_command_backgrounded_mid_run_becomes_bg() -> None:
    """Verify a command backgrounded mid run becomes background work and counts as a job."""
    state = folding.fold(
        *session_fixtures.alive(),
        session_domain.event_shell.ShellStarted(
            session_values.PRIMARY_SHELL_ID,
            session_values.SHELL_COMMAND_CONTENT,
            session_domain.outcomes.ExecutionMode.FOREGROUND,
            None,
        ),
        session_domain.event_shell.ShellBackgrounded(session_values.PRIMARY_SHELL_ID),
    )
    background = actor_access.lead_background(state)
    assert background.running_shell_ids == (session_values.PRIMARY_SHELL_ID,)
    assert background.background_job_count == 1
    # …and it did not move the status: `awaiting_background` is a turn's end.
    assert actor_access.lead_status(state) == session_values.EXECUTING_STATE


def test_monitors_and_bg_jobs_are_counted_apart() -> None:
    """Verify monitors and background jobs are counted apart."""
    state = folding.fold(
        *session_fixtures.alive(),
        session_domain.event_shell.ShellStarted(
            session_domain.ids.ShellId("m1"),
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
        session_domain.ids.ShellId("m1"),
        session_values.BACKGROUND_SHELL_ID,
    }


def test_monitor_output_that_ends_after_turn_releases() -> None:
    """Verify a monitor's output end releases the actor like a background job."""
    assert (
        session_fixtures.status_after(
            session_domain.event_shell.ShellStarted(
                session_domain.ids.ShellId("m1"),
                session_domain.content.TextContent("watch"),
                session_domain.outcomes.ExecutionMode.MONITOR,
                None,
            ),
            session_fixtures.succeeded_turn(),
            session_domain.event_shell.ShellOutputFinished(
                session_domain.ids.ShellId("m1"),
                session_domain.outcomes.Outcome.SUCCEEDED,
            ),
        )
        == session_values.AWAITING_RESPONSE_STATE
    )


def test_finished_session_clears_every_actor_not() -> None:
    """Verify a finished session clears every actor not just the one that ended it."""
    state = folding.fold(
        *session_fixtures.alive(),
        folding.committed(
            session_domain.event_actor.ActorStarted(
                session_values.EXPLORE_TASK_TEXT, session_domain.messaging.ActorRole.CHILD,
            ),
            actor_id=session_values.CHILD,
            parent_actor_id=session_values.LEAD,
            cursor=3,
        ),
        folding.committed(
            session_domain.event_conversation.ReasoningCreated(
                session_domain.ids.ReasoningId("r1"),
                session_domain.content.TextContent("hmm"),
            ),
            actor_id=session_values.CHILD,
            parent_actor_id=session_values.LEAD,
            cursor=4,
        ),
        session_domain.event_session.SessionFinished(session_domain.outcomes.Outcome.SUCCEEDED, None),
    )
    actor_states = dict(state.actors).values()
    assert [actor.status for actor in actor_states] == [None, None]


def test_finished_session_finishes_every_actor() -> None:
    """Verify a finished session finishes every actor not just the lead."""
    state = folding.fold(
        *session_fixtures.alive(),
        folding.committed(
            session_domain.event_actor.ActorStarted(
                session_values.EXPLORE_TASK_TEXT, session_domain.messaging.ActorRole.CHILD,
            ),
            actor_id=session_values.CHILD,
            parent_actor_id=session_values.LEAD,
            cursor=3,
        ),
        folding.committed(
            session_domain.event_session.SessionFinished(session_domain.outcomes.Outcome.SUCCEEDED, None),
            cursor=4,
            occurred_at=session_values.SESSION_FINISH_TIME,
        ),
    )

    assert [(actor.state, actor.finished_at) for actor in state.actors.values()] == [
        (session_values.FINISHED_STATE, session_values.SESSION_FINISH_TIME),
        (session_values.FINISHED_STATE, session_values.SESSION_FINISH_TIME),
    ]
