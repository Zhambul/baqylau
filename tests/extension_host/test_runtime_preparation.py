# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare external workers, commit a complete set, and preserve active reads on failure."""

import sys
from contextlib import closing
from pathlib import Path

import pytest

from extensions.runtime_commit import StoredRegistryCommit
from extensions.runtime_preparation_contract import PreparedExtensionRuntime, RuntimePreparationError
from extensions.worker_errors import WorkerStartError
from tests.extension_api import service_samples as peers
from tests.extension_host import (
    lifecycle_fixture,
    package_fixture,
    runtime_host_fixture as fixtures,
    runtime_query_fixture as queries,
)

ENVIRONMENTS = "environments"


@pytest.mark.parametrize("reverse", [False, True])
def test_complete_candidate_commits_private_peers(
    tmp_path: Path, runtime_wheels: Path, *, reverse: bool,
) -> None:
    """Discovery order does not change dependency preparation or peer service reads."""
    owners = (peers.BETA, peers.ALPHA) if reverse else (peers.ALPHA, peers.BETA)
    fixtures.write_peers(tmp_path, runtime_wheels, owners)
    host = fixtures.host(tmp_path)
    operation = host.accept()
    with closing(host.preparation.prepare_runtime(operation.proposal.candidate)) as prepared:
        assert host.store.read_extension_lifecycle().committed_runtime is None
        queries.publish(host, prepared, operation)
        assert queries.read_peer(host) == peers.PEER_VALUE
        assert host.store.read_extension_lifecycle().committed_runtime == prepared.snapshot.runtime_selection()
        queries.remove(host)
    assert not tuple((tmp_path / ENVIRONMENTS).iterdir())
    assert "peer_backend" not in sys.modules


def test_preparation_uses_captured_source(tmp_path: Path, runtime_wheels: Path) -> None:
    """A source edit after acceptance cannot change selected worker code."""
    sources = fixtures.write_peers(tmp_path, runtime_wheels, (peers.ALPHA,))
    host = fixtures.host(tmp_path)
    operation = host.accept()
    package_fixture.write_file(sources[0], "src/peer_backend.py", b"raise AssertionError('source changed')\n")
    prepared = host.preparation.prepare_runtime(operation.proposal.candidate)
    with closing(prepared):
        queries.publish(host, prepared, operation)
        assert queries.read_peer(host) == "not_installed"
        queries.remove(host)
    with pytest.raises(RuntimePreparationError, match="closed"):
        assert prepared.snapshot


def test_failed_reload_keeps_old_worker(tmp_path: Path, runtime_wheels: Path) -> None:
    """Candidate startup failure closes only fresh workers, not the published set."""
    sources = fixtures.write_peers(tmp_path, runtime_wheels, (peers.ALPHA, peers.BETA))
    host = fixtures.host(tmp_path)
    operation = host.accept()
    with closing(host.preparation.prepare_runtime(operation.proposal.candidate)) as prepared:
        queries.publish(host, prepared, operation)
        package_fixture.write_file(sources[0], "src/peer_backend.py", b"raise ValueError('candidate failed')\n")
        reload = host.accept("reload")
        with pytest.raises(WorkerStartError, match="worker"):
            host.preparation.prepare_runtime(reload.proposal.candidate)
        assert queries.read_peer(host) == peers.PEER_VALUE
        assert host.store.read_extension_lifecycle().committed_runtime == prepared.snapshot.runtime_selection()
        assert len(tuple((tmp_path / ENVIRONMENTS).iterdir())) == len(
            prepared.snapshot.packages,
        )
        queries.finish_failed(host, reload)
        queries.remove(host)
    assert not tuple((tmp_path / ENVIRONMENTS).iterdir())


def test_ready_reload_waits_for_old_reads(tmp_path: Path, runtime_wheels: Path) -> None:
    """Both fresh and active workers survive a busy publication until the next attempt."""
    fixtures.write_peers(tmp_path, runtime_wheels, (peers.ALPHA,))
    host = fixtures.host(tmp_path)
    operation = host.accept()
    with closing(host.preparation.prepare_runtime(operation.proposal.candidate)) as prepared:
        queries.publish(host, prepared, operation)
        _replace_after_read(host, prepared)
    assert not tuple((tmp_path / ENVIRONMENTS).iterdir())


def _replace_after_read(host: fixtures.RuntimeHost, original: PreparedExtensionRuntime) -> None:
    operation = host.accept("reload")
    commit = StoredRegistryCommit(host.store, operation, lifecycle_fixture.NOW + 1)
    with closing(host.preparation.prepare_runtime(operation.proposal.candidate)) as replacement:
        with host.preparation.registry.read_snapshot():
            assert host.preparation.registry.publish_snapshot(1, replacement.snapshot, commit).status == "busy"
            assert queries.read_peer(host) == "not_installed"
            assert replacement.snapshot.directory.runtime_revision != original.snapshot.directory.runtime_revision
        queries.publish(host, replacement, operation, 1)
        original.close()
        assert queries.read_peer(host) == "not_installed"
        queries.remove(host, 2)
