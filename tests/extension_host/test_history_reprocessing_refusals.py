# Copyright (c) 2026 Zhambyl Yermagambet
"""Refuse reprocessing outside the safe boundary, and leave the live history unchanged (C13)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from repository.contract.history_reprocessing import ReprocessingRefusedError, ReprocessingState
from tests.extension_host import history_reprocessing_fixture as fixture

if TYPE_CHECKING:
    from pathlib import Path


def test_open_session_is_refused(tmp_path: Path) -> None:
    """Only a finished session can be reprocessed; the live history does not change."""
    case = fixture.a_case(tmp_path)
    with case.database.write() as connection:
        connection.execute("UPDATE sessions SET lifecycle='running'")

    with pytest.raises(ReprocessingRefusedError, match="finished"):
        case.histories.create(case.session_id)


def test_changed_live_history_refuses_the_switch(tmp_path: Path) -> None:
    """A switch is refused when the live history changed after the request, and the candidate stays ready."""
    case = fixture.a_case(tmp_path)
    live = case.facts()
    candidate = case.histories.create(case.session_id)
    fixture.build(case, candidate.history_revision)
    case.histories.settle(candidate.history_revision, ReprocessingState.SWITCHING)
    with case.database.write() as connection:
        connection.execute(
            "UPDATE history_reprocessings SET live_head=live_head-1 WHERE history_revision=?",
            (candidate.history_revision,),
        )

    case.replay().switch(candidate)

    stored = case.stored(candidate.history_revision)
    assert stored.state == ReprocessingState.READY
    assert stored.diagnostic is not None
    assert case.facts() == live
