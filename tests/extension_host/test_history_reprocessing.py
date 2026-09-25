# Copyright (c) 2026 Zhambyl Yermagambet
"""Replay a finished session into a candidate history, compare it, switch it, and switch back (C13)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from domain.ids import RawEventId
from repository.contract.history_reprocessing import ReprocessingState
from repository.impl.sqlite import interpretations
from tests import storage_reads
from tests.extension_host import history_reprocessing_fixture as fixture

if TYPE_CHECKING:
    from pathlib import Path

PROMPT_INPUT = RawEventId("prompt")


def test_replay_builds_an_equal_candidate(tmp_path: Path) -> None:
    """A replay of the same inputs builds a ready candidate that folds to the same feed."""
    case = fixture.a_case(tmp_path)
    live = case.facts()
    candidate = case.histories.create(case.session_id)

    comparison = fixture.build(case, candidate.history_revision).comparison

    assert case.stored(candidate.history_revision).state == ReprocessingState.READY
    assert comparison is not None
    assert comparison.candidate_facts == comparison.live_facts == len(live)
    assert comparison.equal_entries == comparison.live_entries


def test_replay_runs_no_reaction(tmp_path: Path) -> None:
    """A replay runs no reaction, keeps the live history, and releases the translator's session state."""
    case = fixture.a_case(tmp_path)
    live = case.facts()
    reaction = case.pipeline.core.reaction
    reactions = reaction.react.call_count

    fixture.build(case, case.histories.create(case.session_id).history_revision)

    assert reaction.react.call_count == reactions
    assert case.facts() == live
    plugin = case.pipeline.core.plugin
    plugin.translator.release_session.assert_called_with(case.session_id)


def test_switch_promotes_the_candidate(tmp_path: Path) -> None:
    """The switch makes the candidate default with new cursors and keeps the same feed."""
    switch = fixture.switched(tmp_path)
    live_ids, live_cursors = zip(*switch.live, strict=True)
    now_ids, now_cursors = zip(*switch.case.facts(), strict=True)

    assert switch.case.stored(switch.candidate.history_revision).state == ReprocessingState.ACTIVE
    assert now_ids == live_ids
    assert min(now_cursors) > max(live_cursors)
    assert switch.case.entries() == switch.feed


def test_switch_keeps_the_previous_history(tmp_path: Path) -> None:
    """The previous facts and interpretations stay in the retired history."""
    switch = fixture.switched(tmp_path)
    case = switch.case
    archive = case.archive().history_revision

    assert case.facts(archive) == switch.live
    store = interpretations.SqliteInterpretationRepository(case.database)
    promoted = storage_reads.find_interpretation(store, "default", PROMPT_INPUT)
    assert promoted is not None
    assert promoted.proposal.binding.history_revision == switch.candidate.history_revision
    assert storage_reads.find_interpretation(store, archive, PROMPT_INPUT) is not None


def test_switch_resets_stream_clients(tmp_path: Path) -> None:
    """The switch changes the view revision, keeps the session finished, and leaves no unread fact."""
    switch = fixture.switched(tmp_path)
    case = switch.case

    assert case.view_revision() == switch.view_revision + 1
    with case.database.read() as connection:
        assert connection.execute("SELECT lifecycle FROM sessions").fetchone()[0] == "finished"
    assert case.loop.tick() == 0


def test_switch_back_restores_the_history(tmp_path: Path) -> None:
    """A retired history can become default again with the same switch."""
    switch = fixture.switched(tmp_path)
    case = switch.case
    archive = case.archive()

    case.histories.settle(archive.history_revision, ReprocessingState.SWITCHING)
    case.replay().switch(archive)

    assert case.facts() == switch.live
    assert case.stored(archive.history_revision).state == ReprocessingState.ACTIVE
