# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep source calls inside the actual registry publication boundary."""

from functools import partial
from pathlib import Path
from threading import Event

import pytest

from extensions.registry_snapshot import prepare_snapshot
from tests.extension_host import registry_memory_fixture, source_processing_fixture as fixtures


def test_source_batch_blocks_runtime_publication(tmp_path: Path) -> None:
    """Source work keeps the registry busy until its complete outer batch ends."""
    case = fixtures.installed(tmp_path)
    registry = case.runtime.services.registry
    replacement = prepare_snapshot(100, "replacement", ())
    with case.runtime.capture_batch() as batch:
        assert batch is not None
        batch.read_sources(case.watches, Event().is_set, refresh_plans=True)
        assert registry.publish_snapshot(1, replacement, registry_memory_fixture.MEMORY_COMMIT).status == "busy"
    assert registry.publish_snapshot(1, replacement, registry_memory_fixture.MEMORY_COMMIT).status == "accepted"


def test_failed_batch_releases_registry_reader(tmp_path: Path) -> None:
    """A failed core stage cannot leave the runtime permanently busy.

    Raises:
        RuntimeError: Inside the expected failure context only.

    """
    case = fixtures.installed(tmp_path)
    message = "fixture core stage failed"
    with pytest.raises(RuntimeError, match="fixture"), case.runtime.capture_batch():
        raise RuntimeError(message)
    registry = case.runtime.services.registry
    replacement = prepare_snapshot(100, "replacement", ())
    assert registry.publish_snapshot(1, replacement, registry_memory_fixture.MEMORY_COMMIT).status == "accepted"


def test_mismatched_manager_rejects_source_batch(tmp_path: Path) -> None:
    """Stored package metadata cannot authorize a different live runtime."""
    case = fixtures.installed(tmp_path)
    state = case.manager.read_state.return_value
    case.manager.read_state.return_value = state.model_copy(update={"active_runtime": "different"})
    with pytest.raises(RuntimeError, match="active manager runtime"), case.runtime.capture_batch():
        pytest.fail("a mismatched runtime must not yield a source batch")
    assert not case.probe.trace.reads


def test_failed_initial_restore_has_core_fallback(tmp_path: Path) -> None:
    """Without an active runtime the engine gets an explicit absent source batch."""
    case = fixtures.installed(tmp_path)
    state = case.manager.read_state.return_value
    case.manager.read_state.return_value = state.model_copy(update={"active_runtime": None})
    with case.runtime.capture_batch() as batch:
        assert batch is None
    assert not case.probe.trace.reads


def test_stop_is_checked_between_source_pages(tmp_path: Path) -> None:
    """A source cannot drain further pages after the engine stop condition becomes true."""
    case = fixtures.installed(tmp_path)
    case.probe.behavior.pages = case.runtime.policy.batches_per_source + 1
    with case.runtime.capture_batch() as batch:
        assert batch is not None
        stopped = partial(bool, case.probe.trace.reads)
        due = batch.read_sources(case.watches, stopped, refresh_plans=True)
    assert len(case.probe.trace.reads) == 1
    assert due == case.clock.now + case.runtime.policy.continuation_seconds


@pytest.mark.parametrize("path", ["/", "/baqylau-test-missing-root/input.log"])
def test_filesystem_root_watch_is_rejected(tmp_path: Path, path: str) -> None:
    """A missing top-level source cannot cause a recursive filesystem-root watch."""
    case = fixtures.installed(tmp_path)
    source = case.probe.plan[0].model_copy(update={"watch_paths": (path,)})
    case.probe.plan = (source,)
    assert case.run() == case.clock.now + case.runtime.policy.retry_seconds
    assert not case.watches.paths
    assert not case.probe.trace.reads


def test_symlink_to_root_is_rejected(tmp_path: Path) -> None:
    """A lexical path under a valid parent cannot hide a physical root watch."""
    case = fixtures.installed(tmp_path)
    link = tmp_path / "root-link"
    link.symlink_to("/", target_is_directory=True)
    paths = (str(link),)
    source = case.probe.plan[0].model_copy(update={"watch_paths": paths})
    case.probe.plan = (source,)
    assert case.run() == case.clock.now + case.runtime.policy.retry_seconds
    assert not case.watches.paths
    assert not case.probe.trace.reads
