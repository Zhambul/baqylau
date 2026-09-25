# Copyright (c) 2026 Zhambyl Yermagambet
"""An observer owner with too many open jobs waits; its unread facts stay durable (P03-T05)."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from domain.extension_jobs import JobState
from tests import observer_doubles, observer_pass_fixture, observer_storage_doubles as storage_doubles

if TYPE_CHECKING:
    from pathlib import Path

    from tests.observer_case import ObserverCase


def limited(case: ObserverCase, max_open_jobs: int) -> ObserverCase:
    """Use the same stores with another open-job limit.

    Returns:
        The case with the limited pass.

    """
    return replace(case, observer_pass=replace(case.observer_pass, max_open_jobs=max_open_jobs))


def test_full_owner_waits_and_keeps_the_fact(tmp_path: Path) -> None:
    """With no room, the pass reads nothing; a later pass with room accepts the same fact."""
    case = observer_pass_fixture.a_case(tmp_path)

    assert limited(case, 0).accept() == 0
    assert case.stores.jobs.open_jobs(observer_doubles.OWNER) == 0
    assert limited(case, 1).accept() == 1
    assert case.job().state == JobState.ACCEPTED


def test_room_is_used_then_the_owner_waits(tmp_path: Path) -> None:
    """One free place accepts the one fact; the owner is then full until the job ends."""
    case = limited(observer_pass_fixture.a_case(tmp_path), 1)

    assert case.accept() == 1
    assert case.job().state == JobState.ACCEPTED
    assert case.stores.jobs.open_jobs(observer_doubles.OWNER) == 1
    assert case.cursor() == storage_doubles.COMMIT_CURSOR
