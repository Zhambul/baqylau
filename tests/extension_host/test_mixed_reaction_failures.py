# Copyright (c) 2026 Zhambyl Yermagambet
"""Retain failed core work before any extension-only progress update."""

from pathlib import Path
from unittest.mock import DEFAULT, Mock

import pytest

from core.work_queue import WorkKind
from tests import canonical_sessiondata_fixtures as payloads, sqlite_migration_fixture as snapshots
from tests.extension_host import reaction_fixture as fixture, source_engine_fixture as engines

MIXED_COUNT = 3
FAILURE = "fixture write failed"
APPLY_METHOD = "apply"


@pytest.mark.parametrize("rebuild", [False, True])
def test_failed_core_write_stops_mixed_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, rebuild: bool,
) -> None:
    """An extension tail cannot move progress past a failed core fact."""
    case = fixture.installed(tmp_path)
    fixture.append_extensions(case)
    monkeypatch.setattr(case.view, APPLY_METHOD, Mock(side_effect=RuntimeError(FAILURE)))
    with pytest.raises(RuntimeError, match=FAILURE):
        case.loop.rebuild() if rebuild else case.loop.drain(bool)
    assert case.view.progress() == 0
    assert not case.view.visible()
    case.listener.applied.assert_not_called()
    assert len(case.audit.failures) == 1


def test_engine_retries_after_core_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The actual engine retains failed canonical work and can finish after repair."""
    case = fixture.installed(tmp_path)
    fixture.append_extensions(case)
    engine = engines.engine(monkeypatch)
    engine.worker.reaction_loop = case.loop
    apply = case.view.apply
    monkeypatch.setattr(case.view, APPLY_METHOD, Mock(side_effect=RuntimeError(FAILURE)))
    engine.run({WorkKind.CANONICAL})
    engine.queue.schedule.assert_called_once_with(WorkKind.CANONICAL, 1.0, key="retry")
    assert case.view.progress() == 0
    monkeypatch.setattr(case.view, APPLY_METHOD, apply)
    engine.run({WorkKind.CANONICAL})
    assert case.view.progress() == MIXED_COUNT
    assert not case.loop.tick()


def test_failed_progress_write_is_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A checkpoint failure leaves the extension fact available for another pass."""
    case = fixture.installed(tmp_path, core=False)
    fixture.append_extensions(case)
    advance = case.view.advance_past_extensions
    monkeypatch.setattr(case.view, "advance_past_extensions", Mock(side_effect=RuntimeError(FAILURE)))
    before = snapshots.snapshot(case.view.sqlite_database)
    with pytest.raises(RuntimeError, match=FAILURE):
        case.loop.drain(bool)
    assert snapshots.snapshot(case.view.sqlite_database) == before
    monkeypatch.setattr(case.view, "advance_past_extensions", advance)
    assert case.loop.drain(bool) == 1
    assert case.view.progress() == 1


def test_partial_batch_keeps_committed_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Announce committed actors, stop at the failed core fact, and retain its tail."""
    case = fixture.installed(tmp_path)
    fixture.append_core(case, payloads.succeeded_turn())
    fixture.append_extensions(case)
    apply = case.view.apply
    monkeypatch.setattr(case.view, APPLY_METHOD, Mock(wraps=apply, side_effect=[
        DEFAULT, DEFAULT, RuntimeError(FAILURE),
    ]))
    with pytest.raises(RuntimeError, match=FAILURE):
        case.loop.drain(bool)
    assert case.view.progress() == MIXED_COUNT - 1
    case.listener.applied.assert_called_once()
    monkeypatch.setattr(case.view, APPLY_METHOD, apply)
    assert case.loop.drain(bool) == MIXED_COUNT - 1
    assert case.view.progress() == MIXED_COUNT + 1
