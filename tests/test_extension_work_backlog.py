# Copyright (c) 2026 Zhambyl Yermagambet
"""The signoff backlog names an observer with unread facts, then counts its job until it ends (P08-T01)."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from extensions.work_backlog import BacklogReader
from tests import observer_doubles, observer_pass_fixture

if TYPE_CHECKING:
    from pathlib import Path

    from extensions.projection_pass import ProjectionPass
    from tests.observer_case import ObserverCase


def backlog_of(case: ObserverCase) -> BacklogReader:
    """Read the backlog over the case's real observer and job storage.

    Returns:
        The reader; the case has no projector package, so no projection store is read.

    """
    return BacklogReader(
        projections=cast("ProjectionPass", None), observers=case.observer_pass, jobs=case.stores.jobs,
    )


def test_backlog_follows_observer_work(tmp_path: Path) -> None:
    """Unread facts name the owner; an accepted job is counted; a finished job leaves nothing."""
    case = observer_pass_fixture.a_case(tmp_path)
    reader = backlog_of(case)

    before = reader.read((case.package,))
    assert before.observer_owners == (observer_doubles.OWNER,)
    assert not before.empty
    case.accept()
    accepted = reader.read((case.package,))
    assert (accepted.observer_owners, accepted.open_jobs) == ((), 1)
    case.execute(case.package)
    assert reader.read((case.package,)).empty
