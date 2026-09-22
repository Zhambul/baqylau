# Copyright (c) 2026 Zhambyl Yermagambet
"""Run complete manager changes with feature code in real private worker processes."""

from contextlib import closing
from pathlib import Path

from tests.extension_api import service_samples as peers
from tests.extension_host import (
    manager_assertions as checks,
    manager_fixture as fixtures,
    package_fixture,
    runtime_host_fixture,
    runtime_query_fixture as queries,
)

ENABLE = "enable"


def test_manager_runs_cooperating_workers(tmp_path: Path, runtime_wheels: Path) -> None:
    """Publication and callbacks use the manager's production private worker set."""
    runtime_host_fixture.write_peers(tmp_path, runtime_wheels, (peers.ALPHA, peers.BETA))
    with closing(fixtures.open_manager(tmp_path)) as host:
        host.finish()
        assert host.controller.submit_operation(host.proposal(ENABLE)).status == "accepted"
        host.finish()
        assert queries.read_peer(host.runtime) == peers.PEER_VALUE
        checks.require_operation(host, ENABLE)
    assert not tuple((tmp_path / "environments").iterdir())


def test_manager_records_failed_reload(tmp_path: Path, runtime_wheels: Path) -> None:
    """A candidate failure is stored automatically and leaves the prior worker usable."""
    sources = runtime_host_fixture.write_peers(tmp_path, runtime_wheels, (peers.ALPHA, peers.BETA))
    with closing(fixtures.open_manager(tmp_path)) as host:
        host.finish()
        host.controller.submit_operation(host.proposal(ENABLE))
        host.finish()
        before = host.controller.read_state()
        package_fixture.write_file(sources[0], "src/peer_backend.py", b"raise ValueError('reload failed')\n")
        host.controller.submit_operation(host.proposal("reload"))
        host.finish("failed")
        checks.require_operation(host, "reload", "failed")
        assert host.controller.read_state().active_runtime == before.active_runtime
        assert queries.read_peer(host.runtime) == peers.PEER_VALUE
    assert not tuple((tmp_path / "environments").iterdir())


def test_restart_uses_last_committed_worker_set(tmp_path: Path, runtime_wheels: Path) -> None:
    """A restart needs no original source package and selects a fresh runtime identity."""
    runtime_host_fixture.write_peers(tmp_path, runtime_wheels, (peers.ALPHA, peers.BETA))
    with closing(fixtures.open_manager(tmp_path)) as host:
        host.finish()
        host.controller.submit_operation(host.proposal(ENABLE))
        host.finish()
        before = host.controller.read_state()
    (tmp_path / "packages").rename(tmp_path / "removed-source")
    with closing(fixtures.open_manager(tmp_path)) as restarted:
        restarted.finish()
        assert restarted.controller.read_state().active_runtime != before.active_runtime
        assert queries.read_peer(restarted.runtime) == peers.PEER_VALUE
        restored = checks.committed(restarted.controller.read_state())
        assert restored.packages == checks.committed(before).packages
    assert not tuple((tmp_path / "environments").iterdir())
