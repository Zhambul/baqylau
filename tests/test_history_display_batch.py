# Copyright (c) 2026 Zhambyl Yermagambet
"""Load history without replaying each old display state."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from core.change_signal import ChangeSignal
from domain.event_conversation import TurnStarted
from terminal.tabs import TabColorPainter
from tests import (
    canonical_sessiondata_fixtures as fixtures,
    canonical_sessiondata_loop_support as loop_support,
    canonical_sessiondata_paint_support as paint_support,
    canonical_sessiondata_values as fixture_values,
)

if TYPE_CHECKING:
    from pathlib import Path

    from domain.event_base import EventPayload
    from engine.react.loop import ReactionLoop
    from repository.impl.sqlite.session_data import SqliteSessionDataRepository

HISTORICAL_TURNS = 251


def test_history_pages_announce_final_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep all entries but paint and notify only after the ready history is folded."""
    tabs = paint_support.RecordingTabs()
    payloads = _history_payloads()
    loop, read_model, audit = loop_support.loop_over(
        tmp_path,
        payloads,
        listener=TabColorPainter(tabs, paint_support.FixedSessions(fixture_values.LEAD)),
    )
    read_model.sqlite_database.changes = loop.dependencies.changes

    _drain_with_one_notice(loop, len(payloads), monkeypatch)
    assert [painted[0] for painted in tabs.painted] == [fixture_values.PAINT_TOOL_NAME]
    _assert_history_rows(read_model, len(payloads))
    assert not audit.failures


def _history_payloads() -> tuple[EventPayload, ...]:
    turns = tuple(
        event for _ in range(HISTORICAL_TURNS)
        for event in (TurnStarted(None), fixtures.succeeded_turn())
    )
    return (*fixtures.alive(), *turns, TurnStarted(None))


def _drain_with_one_notice(loop: ReactionLoop, payload_count: int, monkeypatch: pytest.MonkeyPatch) -> None:
    with loop.dependencies.changes.subscribe_thread() as changed:
        wakes = Mock(wraps=changed.set)
        monkeypatch.setattr(changed, "set", wakes)
        assert loop.drain(bool) == payload_count
        wakes.assert_called_once()


def _assert_history_rows(read_model: SqliteSessionDataRepository, payload_count: int) -> None:
    assert fixtures.required_data(read_model).actors[0].status == "thinking"
    entries = read_model.entries_page(fixture_values.SESSION, limit=payload_count).entries
    assert (
        read_model.progress(), len(entries),
    ) == (payload_count, payload_count - len(fixtures.alive()))


def test_change_batch_announces_after_error() -> None:
    """Announce committed changes even when later work fails."""
    signal = ChangeSignal()
    fail = Mock(side_effect=ValueError("failed work"))
    with signal.subscribe_thread() as changed:
        with pytest.raises(ValueError, match="failed work"), signal.batch():  # noqa: PT012 -- Check the committed notice before the failure.
            signal.publish()
            with signal.batch():
                signal.publish()
            assert not changed.is_set()
            fail()
        assert changed.is_set()


def test_history_error_still_notifies_listeners(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Report applied actors when a later history read fails."""
    listener = Mock()
    loop, read_model, audit = loop_support.loop_over(tmp_path, fixtures.alive(), listener=listener)
    monkeypatch.setattr(
        loop.dependencies.canonical_fact_reader,
        "current_fact_page",
        Mock(side_effect=[
            loop.dependencies.canonical_fact_reader.current_fact_page(0, len(fixtures.alive())),
            RuntimeError("history read failed"),
        ]),
    )
    with pytest.raises(RuntimeError, match="history read failed"):
        loop.drain(bool)
    listener.applied.assert_called_once_with(fixture_values.SESSION, fixtures.required_data(read_model).actors)
    assert not audit.failures


def test_empty_change_batch_does_not_wake_readers() -> None:
    """Do not announce a batch without changed data."""
    signal = ChangeSignal()
    with signal.subscribe_thread() as changed, signal.batch():
        assert not changed.is_set()
    assert not changed.is_set()
