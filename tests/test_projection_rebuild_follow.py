# Copyright (c) 2026 Zhambyl Yermagambet
"""A ready candidate generation follows new facts, so it can become live while its sessions run.

A rebuild of the adapters projection became ready, but this session added
facts at once. The candidate stopped at its last fact, the switch needs it as
far as the live generation in every scope, and so it could never become live.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from repository.contract.projection_generations import GenerationState, ProjectionSwitchError
from tests import projection_pass_fixture as fixture, projection_rebuild_fixture as rebuild_fixture

if TYPE_CHECKING:
    from repository.impl.sqlite.connection import SqliteDatabase

NEXT_ID = "fact-two"
NEXT_CURSOR = fixture.FACT_CURSOR + 1
NEXT_HEAD = "UPDATE canonical_scope_heads SET head_cursor=? WHERE scope=?"


def test_a_ready_candidate_follows_new_facts(main: SqliteDatabase) -> None:
    """A fact that the live generation projects after the rebuild reaches the candidate before the switch."""
    case = rebuild_fixture.a_case(main)
    package = rebuild_fixture.a_package(fixture.LocalProjector())
    candidate = case.rebuild.request(fixture.OWNER, (package,))
    case.build(package)
    later = _with_next_fact(case, main)
    assert later.live.run_selected((package,), rebuild_fixture.MAX_PASSES, rebuild_fixture.NO_HEALTH) == 1
    with pytest.raises(ProjectionSwitchError, match="behind"):
        case.generations.switch(candidate.generation)

    later.build(package)

    assert case.generations.switch(candidate.generation).state == GenerationState.ACTIVE


def _with_next_fact(case: rebuild_fixture.RebuildCase, main: SqliteDatabase) -> rebuild_fixture.RebuildCase:
    first = fixture.fact(fixture.FACT_ID, fixture.FACT_CURSOR)
    stored = (first, fixture.fact(NEXT_ID, NEXT_CURSOR))
    live = replace(case.live, facts=fixture.FakeFacts(stored=stored))
    with main.write() as write:
        write.execute(NEXT_HEAD, (NEXT_CURSOR, fixture.SCOPE.model_dump_json()))
    return replace(case, live=live, rebuild=replace(case.rebuild, live=live))
