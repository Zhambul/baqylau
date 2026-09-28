# Copyright (c) 2026 Zhambyl Yermagambet
"""Rebuild one owner's projection into a candidate generation, compare it, and switch it (C12)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from repository.contract.projection_generations import GenerationComparison, GenerationState, ProjectionSwitchError
from repository.impl.sqlite import read_model_views
from tests import projection_pass_fixture as fixture, projection_rebuild_fixture as rebuild_fixture

if TYPE_CHECKING:
    from baqylau_extension_api.models import projections

    from repository.impl.sqlite.connection import SqliteDatabase

LIVE_ENTRIES = ("Card summary", "Card detail")
LIVE_GENERATION = "default"
LIVE_VIEW = (LIVE_ENTRIES, LIVE_GENERATION)
EQUAL = GenerationComparison(
    live_records=1, candidate_records=1, equal_records=1, live_entries=2, candidate_entries=2, equal_entries=2,
)


@dataclass(frozen=True)
class FailingProjector(fixture.LocalProjector):
    """Select keys, then fail every projection."""

    def project(self, projection_request: projections.ProjectionRequest) -> projections.ProjectionResult:
        """Fail before any result.

        Raises:
            RuntimeError: Always.

        """
        cursor = projection_request.binding.context.input_cursor
        message = f"projection failed at {cursor}"
        raise RuntimeError(message)


def test_rebuild_builds_an_equal_candidate(main: SqliteDatabase) -> None:
    """A candidate is built from stored facts and compares equal; live rows do not change."""
    case = rebuild_fixture.a_case(main)
    package = rebuild_fixture.a_package(fixture.LocalProjector())
    candidate = case.rebuild.request(fixture.OWNER, (package,))

    assert case.build(package) == 0

    built = case.generations.read(candidate.generation)
    assert built is not None
    assert built.state == GenerationState.READY
    assert built.comparison == EQUAL
    assert case.live_view() == LIVE_VIEW


def test_switch_makes_the_candidate_live(main: SqliteDatabase) -> None:
    """A switch moves the candidate rows live in one transaction and resets session streams."""
    case = rebuild_fixture.a_case(main)
    package = rebuild_fixture.a_package(fixture.LocalProjector())
    candidate = case.rebuild.request(fixture.OWNER, (package,))
    case.build(package)
    with main.read() as connection:
        before = read_model_views.revision(connection)

    assert case.generations.switch(candidate.generation).state == GenerationState.ACTIVE

    assert case.live_view() == (LIVE_ENTRIES, candidate.generation)
    with main.read() as connection:
        assert read_model_views.revision(connection) == before + 1


def test_switch_keeps_the_previous_generation(main: SqliteDatabase) -> None:
    """The previous live generation is retired and keeps its feed rows as candidate rows."""
    case = rebuild_fixture.a_case(main)
    package = rebuild_fixture.a_package(fixture.LocalProjector())
    candidate = case.rebuild.request(fixture.OWNER, (package,))
    case.build(package)

    case.generations.switch(candidate.generation)

    previous = case.generations.read(f"{LIVE_GENERATION}:{fixture.OWNER}")
    assert previous is not None
    assert previous.state == GenerationState.RETIRED
    assert len(case.candidate_entries(previous.generation)) == len(LIVE_ENTRIES)


def test_two_rebuilds_of_the_same_facts_are_equal(main: SqliteDatabase) -> None:
    """Two candidates from the same facts store the same logical IDs and content (C12)."""
    case = rebuild_fixture.a_case(main)
    package = rebuild_fixture.a_package(fixture.LocalProjector())
    first = case.rebuild.request(fixture.OWNER, (package,))
    second = case.rebuild.request(fixture.OWNER, (package,))
    case.build(package)

    first_rows = case.candidate_entries(first.generation)
    assert first_rows == case.candidate_entries(second.generation)
    assert len(first_rows) == len(LIVE_ENTRIES)


def test_failed_rebuild_keeps_the_live_generation(main: SqliteDatabase) -> None:
    """A failing projector fails the candidate, and the switch is refused."""
    case = rebuild_fixture.a_case(main)
    failing = rebuild_fixture.a_package(FailingProjector())
    candidate = case.rebuild.request(fixture.OWNER, (failing,))

    assert case.build(failing) == 1

    failed = case.generations.read(candidate.generation)
    assert failed is not None
    assert failed.state == GenerationState.FAILED
    with pytest.raises(ProjectionSwitchError):
        case.generations.switch(candidate.generation)
    assert case.live_view() == LIVE_VIEW
