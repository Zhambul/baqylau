# Copyright (c) 2026 Zhambyl Yermagambet
"""Test canonical sessiondata status attention."""

from __future__ import annotations

from tests import canonical_sessiondata_fixtures as session_fixtures, canonical_sessiondata_values as session_values
from tests.canonical_sessiondata_components import domain as session_domain

COMPACTED_ITEMS = 200


def test_unanswered_question_outlives_work_that() -> None:
    """Verify an unanswered question outlives the work that finished after it.

    The pending set is why: a finish has to know whether anybody is still
        waiting, and no single fact can say so.
    """
    assert (
        session_fixtures.status_after(session_domain.event_work.QuestionAsked(session_values.QUESTION_ATTENTION_ID, ()))
        == "awaiting_attention"
    )
    assert (
        session_fixtures.status_after(
            session_domain.event_work.QuestionAsked(session_values.QUESTION_ATTENTION_ID, ()),
            session_domain.event_shell.ShellFinished(
                session_values.PRIMARY_SHELL_ID,
                session_domain.outcomes.Outcome.SUCCEEDED,
                None,
                0,
            ),
        )
        == "awaiting_attention"
    )
    assert (
        session_fixtures.status_after(
            session_domain.event_work.QuestionAsked(session_values.QUESTION_ATTENTION_ID, ()),
            session_domain.event_work.QuestionAnswered(session_values.QUESTION_ATTENTION_ID, (), None),
            session_domain.event_shell.ShellFinished(
                session_values.PRIMARY_SHELL_ID,
                session_domain.outcomes.Outcome.SUCCEEDED,
                None,
                0,
            ),
        )
        == session_values.WORKING_STATE
    )


def test_plan_waits_for_person_same_way_question() -> None:
    """Verify a plan waits for a person the same way a question does."""
    assert (
        session_fixtures.status_after(
            session_domain.event_work.PlanProposed(
                session_values.QUESTION_ATTENTION_ID,
                session_domain.content.TextContent("do it"),
            ),
        )
        == "awaiting_attention"
    )
    assert (
        session_fixtures.status_after(
            session_domain.event_work.PlanProposed(
                session_values.QUESTION_ATTENTION_ID,
                session_domain.content.TextContent("do it"),
            ),
            session_domain.event_work.PlanResolved(
                attention_id=session_values.QUESTION_ATTENTION_ID,
                state=session_domain.outcomes.PlanState.APPROVED,
                feedback=None,
                edited=False,
            ),
        )
        == session_values.WORKING_STATE
    )


def test_compaction_is_work() -> None:
    """Verify compaction is work."""
    assert (
        session_fixtures.status_after(session_domain.event_telemetry.CompactionStarted(1000))
        == session_values.WORKING_STATE
    )


def test_finished_compaction_settles_status() -> None:
    """Verify finished compaction settles status outside an active turn."""
    assert (
        session_fixtures.status_after(
            session_fixtures.succeeded_turn(),
            session_domain.event_telemetry.CompactionStarted(1000),
            session_domain.event_telemetry.CompactionFinished(1000, COMPACTED_ITEMS, None),
        )
        == session_values.AWAITING_RESPONSE_STATE
    )


def test_compaction_finish_keeps_turn_working() -> None:
    """Verify finished compaction does not settle an active turn."""
    assert (
        session_fixtures.status_after(
            session_fixtures.succeeded_turn(),
            session_domain.event_conversation.TurnStarted(None),
            session_domain.event_telemetry.CompactionStarted(1000),
            session_domain.event_telemetry.CompactionFinished(1000, COMPACTED_ITEMS, None),
        )
        == session_values.WORKING_STATE
    )


def test_turn_that_ends_with_nothing_running() -> None:
    """Verify a turn that ends with nothing running is awaiting a response."""
    assert (
        session_fixtures.status_after(
            session_domain.event_conversation.TurnStarted(None),
            session_fixtures.succeeded_turn(),
        )
        == session_values.AWAITING_RESPONSE_STATE
    )
    assert (
        session_fixtures.status_after(
            session_domain.event_conversation.TurnStarted(None),
            session_domain.event_conversation.TurnAborted(None),
        )
        == session_values.AWAITING_RESPONSE_STATE
    )


def test_turn_that_ends_over_running_bg_job() -> None:
    """Verify a turn that ends over a running background job is awaiting it.

    The state that used to be unreachable. A background job's launch reports
        finished immediately, while its output still flows — so ending it there
        emptied the set before a turn could ever end on it, and a session with a job
        still running read as idle.
    """
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
        )
        == "awaiting_background"
    )
