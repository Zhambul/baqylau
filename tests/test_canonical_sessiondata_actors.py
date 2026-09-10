# Copyright (c) 2026 Zhambyl Yermagambet
"""Test canonical sessiondata actors."""

from __future__ import annotations

from tests import (
    canonical_sessiondata_actor_access as actor_access,
    canonical_sessiondata_components as sessiondata_components,
    canonical_sessiondata_fixtures as session_fixtures,
    canonical_sessiondata_folding as folding,
    canonical_sessiondata_values as session_values,
)
from tests.canonical_sessiondata_components import domain as session_domain


def test_actor_is_born_once_and_reopens_rather() -> None:
    """Verify an actor is born once and reopens rather than forgetting.

    Both evidence streams announce a subagent, so the second announcement is
        the same actor — and it must not discard what the first one learned.
    """
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
            session_domain.event_actor.ActorFinished(None),
            actor_id=session_values.CHILD,
            parent_actor_id=session_values.LEAD,
            cursor=4,
            occurred_at=session_values.ACTOR_FINISH_TIME,
        ),
        folding.committed(
            session_domain.event_actor.ActorStarted(
                session_values.EXPLORE_TASK_TEXT, session_domain.messaging.ActorRole.CHILD,
            ),
            actor_id=session_values.CHILD,
            parent_actor_id=session_values.LEAD,
            cursor=5,
        ),
    )
    child = folding.actor_from(state, session_values.CHILD)
    assert (child.role, child.name, child.parent_actor_id) == (
        "child",
        session_values.EXPLORE_TASK_TEXT,
        session_values.LEAD,
    )
    assert (child.state, child.finished_at) == (session_values.RUNNING_STATE, None)


def test_actor_carries_one_model_name_and_its() -> None:
    """Verify an actor carries one model name and its effort."""
    state = folding.fold(
        *session_fixtures.alive(),
        session_domain.event_session.ModelChanged(
            None,
            session_domain.references.ModelReference(session_values.OPUS_MODEL_ID, session_values.OPUS_MODEL_NAME),
            session_domain.work_state.ModelChangeReason.SELECTED,
        ),
        session_domain.event_session.EffortChanged(
            None, session_values.HIGH_EFFORT, session_domain.work_state.EffortChangeReason.SELECTED,
        ),
    )
    # The whole reference is kept: a reader is shown the display name, and a
    # relaunch needs the harness's own id for the same model.
    assert actor_access.lead_model(state) == session_domain.references.ModelReference(
        session_values.OPUS_MODEL_ID, session_values.OPUS_MODEL_NAME,
    )
    assert actor_access.lead_effort(state) == session_values.HIGH_EFFORT


def test_model_with_no_display_name_still_records() -> None:
    """Verify a model with no display name still records its name."""
    state = folding.fold(
        *session_fixtures.alive(),
        session_domain.event_session.ModelChanged(
            None,
            session_domain.references.ModelReference("gpt-5.4", None),
            session_domain.work_state.ModelChangeReason.SELECTED,
        ),
    )
    model = actor_access.lead_model(state)
    assert model is not None
    assert model.name == "gpt-5.4"


def test_harness_namer_settles_display_at_fold() -> None:
    """Verify the harness namer settles the display at fold time.

    ONE model name everywhere: an alias-only reference (the launch, before
        the harness reports the resolved id) must already show the name the
        resolved id will show — the exact bug where one actor said session_values.SONNET_MODEL_ID while
        its refined neighbour said session_values.SONNET_MODEL_NAME.
    """
    writer = sessiondata_components.engine.actors.ActorWriter(
        sessiondata_components.engine.naming.ModelNaming({
            session_domain.ids.HarnessName.CODEX: sessiondata_components.harness.model_names.display_model,
        }),
    )
    state = sessiondata_components.engine.contract.AggregateState()
    for payload in (
        *session_fixtures.alive(),
        session_domain.event_session.ModelChanged(
            None,
            session_domain.references.ModelReference(session_values.SONNET_MODEL_ID, None),
            session_domain.work_state.ModelChangeReason.SELECTED,
        ),
    ):
        state = writer.write(folding.committed(payload), state)
    model = actor_access.lead_model(state)
    assert model is not None
    assert model.display_name == session_values.SONNET_MODEL_NAME


def test_the_claude_namer_speaks_one_vocabulary() -> None:
    """Verify the claude namer speaks one vocabulary."""
    assert (
        sessiondata_components.harness.model_names.display_model(
            session_domain.references.ModelReference("claude-sonnet-5", None),
        )
        == session_values.SONNET_MODEL_NAME
    )
    assert (
        sessiondata_components.harness.model_names.display_model(
            session_domain.references.ModelReference(session_values.SONNET_MODEL_ID, None),
        )
        == session_values.SONNET_MODEL_NAME
    )
    assert (
        sessiondata_components.harness.model_names.display_model(
            session_domain.references.ModelReference("claude-haiku-4-5-20251001", None),
        )
        == "haiku-4.5"
    )
    # the PICKER offers the same strings, keyed by the alias the harness takes
    assert {option.model_name: option.display_name for option in sessiondata_components.harness.plugin.MODELS} == {
        "fable": "fable-5",
        "opus": "opus-5",
        session_values.SONNET_MODEL_ID: session_values.SONNET_MODEL_NAME,
        "haiku": "haiku-4.5",
        "claude-opus-4-6": "opus-4.6",
    }


def test_nothing_but_actor_writer_invents_actor() -> None:
    """Verify nothing but the actor writer invents an actor.

    A fact about an actor nobody announced is a fact about a name we cannot
        describe; the row waits for the announcement rather than guessing.
    """
    state = folding.fold(
        session_fixtures.started(),
        folding.committed(
            session_domain.event_telemetry.ContextReported(
                10, session_values.UNKNOWN_ACTOR_CONTEXT_WINDOW_TOKENS, None,
            ),
            actor_id=session_values.CHILD,
            cursor=2,
        ),
        folding.committed(
            session_domain.event_telemetry.UsageReported(
                scope=session_domain.usage.UsageScope.ACTOR,
                subject_id="child-one",
                model=None,
                account=None,
                tokens=session_domain.usage.TokenUsage(5),
                cumulative=True,
                cost_in_usd=None,
            ),
            actor_id=session_values.CHILD,
            cursor=3,
        ),
    )
    assert state.actor(session_values.CHILD) is None
