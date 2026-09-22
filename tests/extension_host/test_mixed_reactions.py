# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep core reactions and read models on the complete mixed cursor stream."""

from pathlib import Path

import pytest
from baqylau_extension_api.models import scopes

from engine.react.loop_runtime import REACTION_BATCH_SIZE
from tests import canonical_sessiondata_fixtures as payloads
from tests.extension_host import interpretation_snapshot_fixture as storage, reaction_fixture as fixture

STARTUP_COUNT = 2
MIXED_COUNT = 3
AFTER_SECOND_CORE = 4
LARGE_COUNT = REACTION_BATCH_SIZE + 1


def test_extension_only_tail_advances(tmp_path: Path) -> None:
    """Only core facts enter side effects, writers, and actor notices."""
    case = fixture.installed(tmp_path)
    fixture.append_extensions(case)
    before = case.store.current_fact_page(0, 10)
    assert case.loop.drain(bool) == MIXED_COUNT
    assert case.view.progress() == MIXED_COUNT
    assert case.view.high_water_cursor() == STARTUP_COUNT
    assert len(case.reaction.seen) == STARTUP_COUNT
    assert case.store.current_fact_page(0, 10) == before


@pytest.mark.parametrize("scope", [
    scopes.InstallationScope(),
    scopes.RepositoryScope(repository_id="project", worktree="/project", git_directory="/project/.git"),
    scopes.SessionScope(session_id="session", actor_id="actor", harness="test"),
])
def test_extension_only_history_calls_no_core(tmp_path: Path, scope: scopes.ExtensionScope) -> None:
    """No feature worker, harness lookup, or fake session is needed to skip a fact."""
    case = fixture.installed(tmp_path, core=False)
    storage.seed(case.store, (storage.fact("extension", scope),))
    with case.loop.dependencies.changes.subscribe_thread() as changed:
        assert case.loop.drain(bool) == 1
        assert not changed.is_set()
    assert not case.reaction.seen and not case.view.visible()
    case.listener.applied.assert_not_called()
    assert not case.audit.failures


def test_one_page_does_not_jump_to_history_head(tmp_path: Path) -> None:
    """An extension-only full page must not skip the next fact."""
    case = fixture.installed(tmp_path, core=False)
    fixture.append_extensions(case, LARGE_COUNT)
    assert case.loop.tick() == REACTION_BATCH_SIZE
    assert case.view.progress() == REACTION_BATCH_SIZE
    assert case.loop.tick() == 1
    assert case.view.progress() == LARGE_COUNT
    assert case.loop.tick() == 0


def test_core_after_extension_page_is_processed(tmp_path: Path) -> None:
    """A later core fact is reached only after the preceding mixed page."""
    case = fixture.installed(tmp_path, core=False)
    fixture.append_extensions(case, REACTION_BATCH_SIZE)
    fixture.append_core(case, payloads.started())
    assert case.loop.tick() == REACTION_BATCH_SIZE
    assert not case.reaction.seen
    assert case.loop.tick() == 1
    assert case.view.progress() == LARGE_COUNT
    assert len(case.view.visible()) == 1


def test_later_core_fact_keeps_accepted_cursor(tmp_path: Path) -> None:
    """A mixed gap does not renumber core facts or restore a synthetic cursor."""
    case = fixture.installed(tmp_path)
    fixture.append_extensions(case)
    fixture.append_core(case, payloads.succeeded_turn())
    assert case.loop.drain(bool) == AFTER_SECOND_CORE
    assert case.view.progress() == AFTER_SECOND_CORE
    assert len(case.reaction.seen) == MIXED_COUNT
    assert case.loop.tick() == 0


def test_candidate_facts_do_not_enter_live_loop(tmp_path: Path) -> None:
    """A candidate-only tail is outside both live reads and core progress."""
    case = fixture.installed(tmp_path)
    storage.seed(case.store, (storage.fact("candidate"),), "candidate")
    assert case.loop.drain(bool) == STARTUP_COUNT
    assert case.view.progress() == STARTUP_COUNT
    assert case.loop.tick() == 0


@pytest.mark.parametrize("core", [False, True])
def test_rebuild_keeps_mixed_progress(tmp_path: Path, *, core: bool) -> None:
    """A core read-model rebuild skips extension facts without repeating side effects."""
    case = fixture.installed(tmp_path, core=core)
    fixture.append_extensions(case)
    consumed = case.loop.drain(bool)
    seen = tuple(case.reaction.seen)
    assert case.loop.rebuild() == consumed
    assert case.view.progress() == consumed
    assert tuple(case.reaction.seen) == seen
    assert case.loop.tick() == 0
