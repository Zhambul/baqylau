# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep complete engine batches behind startup restoration and runtime publication."""

from core.work_queue import WorkKind, WorkQueue
from engine.extensions_boundary import EngineExtensionBoundary
from extensions.models.manager import ManagerProgress
from tests.extension_host.engine_boundary_fixture import BoundaryDouble

RAW_WORK = frozenset((WorkKind.RAW,))


def test_initial_restore_defers_core_work() -> None:
    """A ready notice releases all saved work, not only the latest queue set."""
    boundary = BoundaryDouble(ManagerProgress(status="preparing", registry_revision=0, allow_processing=False))
    engine = EngineExtensionBoundary(boundary, WorkQueue(), boundary.record_failure)
    assert not engine.prepare(set(RAW_WORK))
    assert not engine.prepare({WorkKind.SOURCES})
    boundary.progress = ManagerProgress(status="published", registry_revision=1)
    assert engine.prepare({WorkKind.EXTENSIONS}) == {WorkKind.RAW, WorkKind.SOURCES}
    assert not engine.prepare(set())


def test_preparation_keeps_old_processing_ready() -> None:
    """Reload preparation does not pause processing with the existing active set."""
    boundary = BoundaryDouble(ManagerProgress(status="preparing", registry_revision=1))
    engine = EngineExtensionBoundary(boundary, WorkQueue(), boundary.record_failure)
    assert engine.prepare({WorkKind.EXTENSIONS, WorkKind.RAW}) == RAW_WORK
    assert engine.prepare({WorkKind.CANONICAL}) == {WorkKind.CANONICAL}
    assert boundary.calls == 1


def test_busy_publication_schedules_retry() -> None:
    """External readers delay a change without holding the engine or losing work."""
    boundary = BoundaryDouble(ManagerProgress(status="busy", registry_revision=1))
    queue = WorkQueue()
    engine = EngineExtensionBoundary(boundary, queue, boundary.record_failure)
    assert engine.prepare(set(RAW_WORK)) == RAW_WORK
    assert queue.take() == {WorkKind.EXTENSIONS}


def test_failed_commit_keeps_old_readiness() -> None:
    """A failed stored commit does not discard the old active processing set."""
    boundary = BoundaryDouble()
    queue = WorkQueue()
    engine = EngineExtensionBoundary(boundary, queue, boundary.record_failure)
    engine.prepare({WorkKind.EXTENSIONS})
    boundary.fail_publication = True
    assert engine.prepare({WorkKind.EXTENSIONS, WorkKind.RAW}) == RAW_WORK
    assert boundary.failures == 1
    assert queue.take() == {WorkKind.EXTENSIONS}


def test_startup_commit_failure_retains_work() -> None:
    """An unknown initial runtime keeps work queued until startup can resolve it."""
    boundary = BoundaryDouble(fail_publication=True)
    engine = EngineExtensionBoundary(boundary, WorkQueue(), boundary.record_failure)
    assert not engine.prepare(set(RAW_WORK))
    boundary.fail_publication = False
    assert engine.prepare({WorkKind.EXTENSIONS}) == RAW_WORK


def test_failed_restore_allows_core_fallback() -> None:
    """A recorded startup preparation failure does not stop core input processing."""
    boundary = BoundaryDouble(ManagerProgress(status="failed", registry_revision=0))
    engine = EngineExtensionBoundary(boundary, WorkQueue(), boundary.record_failure)
    assert engine.prepare(set(RAW_WORK)) == RAW_WORK


def test_no_extension_manager_keeps_core_behavior() -> None:
    """Existing standalone engine consumers need no extension runtime owner."""
    boundary = BoundaryDouble()
    engine = EngineExtensionBoundary(None, WorkQueue(), boundary.record_failure)
    expected = {WorkKind.SOURCES, WorkKind.EXTENSION_SOURCES, WorkKind.RAW, WorkKind.CANONICAL}
    assert engine.prepare(set(WorkKind)) == expected
    assert boundary.calls == 0
