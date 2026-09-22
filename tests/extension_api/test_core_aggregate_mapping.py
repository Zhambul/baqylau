# Copyright (c) 2026 Zhambyl Yermagambet
"""Preserve complete core aggregate rows, including nested calculation state."""

from dataclasses import fields
from decimal import Decimal

import pytest
from baqylau_extension_api.core import actor_state as public_actors, session_state as public_sessions
from baqylau_extension_api.core.aggregate import CoreAggregateState
from baqylau_extension_api.core.base import CoreModel
from pydantic import TypeAdapter

from domain import actor_state, session_state
from extensions.mapper import core_aggregates
from tests.extension_api import core_projection_samples

type RowPair = tuple[type[CoreModel], type]
ROW_TYPES: tuple[RowPair, ...] = (
    (public_actors.CoreActorFacts, actor_state.ActorFacts), (public_actors.ActorUsage, actor_state.ActorUsage),
    (public_actors.ActorContext, actor_state.ActorContext),
    (public_actors.ActorBackground, actor_state.ActorBackground),
    (public_actors.ActorStatistics, actor_state.ActorStatistics), (public_actors.ToolCount, actor_state.ToolCount),
    (public_sessions.CoreSessionFacts, session_state.SessionFacts),
    (public_sessions.SessionGoal, session_state.SessionGoal),
    (public_sessions.SessionTask, session_state.SessionTask),
)


@pytest.mark.parametrize(("public_type", "private_type"), ROW_TYPES)
def test_aggregate_fields_match_private_rows(public_type: type[CoreModel], private_type: type) -> None:
    """Require exact field coverage for each aggregate and nested value type."""
    assert set(public_type.model_fields) == {field.name for field in fields(private_type)}


def test_actor_aggregate_round_trip() -> None:
    """Keep all actor fields, exact decimal cost, and internal state."""
    fixture = core_projection_samples.AGGREGATE.actors[0]
    original = TypeAdapter(actor_state.ActorFacts).validate_json(fixture.model_dump_json())
    mapped = core_aggregates.public_actor(original)
    assert mapped == fixture
    assert mapped.usage.cost_in_usd == Decimal("0.12345678901234567890123456789")
    assert core_aggregates.private_actor(mapped) == original


def test_session_aggregate_round_trip() -> None:
    """Keep task, goal, account, and title state through a complete wire round trip."""
    fixture = core_projection_samples.AGGREGATE.session
    assert fixture is not None
    original = TypeAdapter(session_state.SessionFacts).validate_json(fixture.model_dump_json())
    mapped = core_aggregates.public_session(original)
    assert mapped == fixture
    assert core_aggregates.private_session(mapped) == original


def test_core_aggregate_snapshot_round_trip() -> None:
    """Use the complete closed snapshot with no repository or callback object."""
    fixture = core_projection_samples.AGGREGATE
    assert CoreAggregateState.model_validate_json(fixture.model_dump_json()) == fixture
    assert CoreAggregateState().session is None
    assert CoreAggregateState().actors == ()
