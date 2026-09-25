# Copyright (c) 2026 Zhambyl Yermagambet
"""Use repository scope leases without a core session or actor."""

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock

from baqylau_extension_api.models.scopes import RepositoryScope

from tests import storage_reads
from tests.extension_host import source_processing_fixture as fixtures

REPOSITORY = RepositoryScope(repository_id="project", worktree="/project", git_directory="/project/.git")


def test_repository_scope_reads_without_session(tmp_path: Path) -> None:
    """A host-owned repository scope reads and stores its own original input."""
    case = fixtures.installed(tmp_path)
    with case.scopes.hold_scope(REPOSITORY):
        assert case.run() is None
        contexts = tuple(request.context.binding.scope for request in case.probe.trace.reads)
        assert REPOSITORY in contexts
        rows = storage_reads.observations_for_scope(case.original.original.store, REPOSITORY, 0, 10)
        assert len(rows) == 1
    case.run()
    release = case.probe.trace.releases[0]
    assert release.binding.scope == REPOSITORY
    assert release.source_identity is None


def test_scope_release_retries_before_reentry(tmp_path: Path) -> None:
    """Reopening a scope cannot skip cleanup that is still pending for its old use."""
    case = fixtures.installed(tmp_path)
    with case.scopes.hold_scope(REPOSITORY):
        case.run()
    case.probe.behavior.pending_release = True
    due = case.run()
    descriptions = tuple(case.probe.trace.descriptions)
    with case.scopes.hold_scope(REPOSITORY):
        assert case.run(refresh=False) == due
        assert tuple(case.probe.trace.descriptions) == descriptions
        case.probe.behavior.pending_release = False
        case.clock.now += case.runtime.policy.retry_seconds
        assert case.run(refresh=False) is None
    release = case.probe.trace.releases[-1]
    assert release.binding.scope == REPOSITORY
    assert release.source_identity is None


def test_scope_change_after_capture_sends_notice(tmp_path: Path) -> None:
    """Core reactions can add a source scope after the pass has captured its selection."""
    case = fixtures.installed(tmp_path)
    changed = case.runtime.callbacks.changed
    assert isinstance(changed, Mock)
    with ExitStack() as leases:
        with case.runtime.capture_batch():
            leases.enter_context(case.scopes.hold_scope(REPOSITORY))
            changed.reset_mock()
        changed.assert_called_once()
    assert REPOSITORY not in case.scopes.source_scopes()
