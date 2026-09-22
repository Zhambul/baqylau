# Copyright (c) 2026 Zhambyl Yermagambet
"""Check source notices and runtime lifetime through the real engine stages."""

from functools import partial
from pathlib import Path
from unittest.mock import call

import pytest

from core.input_paths import InputGroup
from core.work_queue import WorkKind
from engine import mixed_processing
from engine.source_processing import SOURCE_DEADLINE_KEY
from extensions.registry_snapshot import prepare_snapshot
from tests.extension_host import registry_memory_fixture, source_engine_fixture, source_processing_fixture


def test_source_timer_skips_core_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A source deadline reads extensions and drains data without scanning harness inputs."""
    source = source_processing_fixture.installed(tmp_path)
    engine = source_engine_fixture.engine(monkeypatch, source.runtime)
    engine.run({WorkKind.EXTENSION_SOURCES})
    engine.interpreter.read_sources.assert_not_called()
    engine.interpreter.translation.translate.assert_not_called()
    engine.interpreter.translation.accept_interpretation.assert_called_once()
    engine.reactions.drain.assert_called_once()
    assert len(source.probe.trace.reads) == 1


def test_source_notices_share_one_pass(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file notice absorbs a simultaneous extension deadline without a duplicate read."""
    source = source_processing_fixture.installed(tmp_path)
    engine = source_engine_fixture.engine(monkeypatch, source.runtime)
    engine.run({WorkKind.SOURCES, WorkKind.EXTENSION_SOURCES})
    engine.interpreter.read_sources.assert_called_once()
    assert len(source.probe.trace.descriptions) == 1
    assert len(source.probe.trace.reads) == 1
    engine.reactions.drain.assert_called_once()


def test_batch_keeps_runtime_through_core_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Raw translation and canonical reactions use the same retained runtime as sources."""
    source = source_processing_fixture.installed(tmp_path)
    engine = source_engine_fixture.engine(monkeypatch, source.runtime)
    check = partial(source_engine_fixture.require_busy, source)
    engine.interpreter.translation.accept_interpretation.side_effect = partial(
        source_engine_fixture.accepted_busy, source,
    )
    engine.reactions.drain.side_effect = check
    engine.run({WorkKind.SOURCES})
    engine.interpreter.translation.accept_interpretation.assert_called_once()
    engine.reactions.drain.assert_called_once()
    engine.interpreter.failures.record.assert_not_called()
    registry = source.runtime.services.registry
    replacement = prepare_snapshot(100, "replacement", ())
    assert registry.publish_snapshot(1, replacement, registry_memory_fixture.MEMORY_COMMIT).status == "accepted"


def test_source_failure_keeps_core_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected source call does not prevent the core stages or lose its retry deadline."""
    source = source_processing_fixture.installed(tmp_path)
    source.original.original.store.append_observations(source.original.original.request)
    source.probe.behavior.fail_read = True
    engine = source_engine_fixture.engine(monkeypatch, source.runtime)
    engine.run({WorkKind.SOURCES})
    engine.interpreter.translation.accept_interpretation.assert_called_once()
    engine.reactions.drain.assert_called_once()
    assert engine.queue.set_deadline.call_args_list == [
        call(WorkKind.EXTENSION_SOURCES, source.runtime.policy.retry_seconds, SOURCE_DEADLINE_KEY),
        call(WorkKind.RAW, mixed_processing.CONTINUATION_SECONDS, mixed_processing.CONTINUATION_KEY),
    ]


def test_absent_runtime_clears_only_source_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The core-only path clears extension watches and timers without a dummy worker."""
    engine = source_engine_fixture.engine(monkeypatch)
    engine.run({WorkKind.EXTENSION_SOURCES})
    engine.inputs.watch_files.assert_called_once_with(set(), input_group=InputGroup.ADDITIONAL)
    engine.queue.set_deadline.assert_called_once_with(WorkKind.EXTENSION_SOURCES, None, SOURCE_DEADLINE_KEY)
    engine.interpreter.read_sources.assert_not_called()


def test_failed_capture_retains_full_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed runtime capture retries all dependent stages before making any source call."""
    source = source_processing_fixture.installed(tmp_path)
    source.manager.read_state.side_effect = RuntimeError("fixture manager failed")
    engine = source_engine_fixture.engine(monkeypatch, source.runtime)
    engine.run({WorkKind.SOURCES})
    engine.interpreter.read_sources.assert_not_called()
    engine.reactions.drain.assert_not_called()
    retries = {call.args[0] for call in engine.queue.schedule.call_args_list}
    assert retries == {WorkKind.SOURCES, WorkKind.RAW, WorkKind.CANONICAL}
