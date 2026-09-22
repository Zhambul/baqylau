# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject invalid complete candidates before any process environment is created."""

from contextlib import closing
from pathlib import Path

import pytest
from baqylau_extension_api.errors import ExtensionContractError

from extensions.models.lifecycle_selection import RuntimeSelection
from extensions.runtime_preparation_contract import RuntimePreparationError
from tests.extension_api import service_samples as peers
from tests.extension_host import package_fixture, runtime_host_fixture as fixtures, runtime_query_fixture as queries

ENVIRONMENTS = "environments"


def test_empty_candidate_needs_no_worker(tmp_path: Path) -> None:
    """The normal preparation contract also prepares complete removal."""
    host = fixtures.host(tmp_path)
    selection = RuntimeSelection(runtime_revision="empty", catalog_revision=0)
    with closing(host.preparation.prepare_runtime(selection)) as prepared:
        assert prepared.snapshot.runtime_selection() == selection
        assert not prepared.snapshot.active_order
    assert not (tmp_path / ENVIRONMENTS).exists()


def test_web_only_candidate_needs_no_worker(tmp_path: Path) -> None:
    """A web package has a selected environment identity but no Python process."""
    package_fixture.write_package(tmp_path / "packages", web=True)
    host = fixtures.host(tmp_path)
    operation = host.accept()
    with closing(host.preparation.prepare_runtime(operation.proposal.candidate)) as prepared:
        assert prepared.snapshot.packages[0].environment is not None
        assert prepared.snapshot.packages[0].plugin is None
        queries.publish(host, prepared, operation)
        queries.remove(host)
    assert not (tmp_path / ENVIRONMENTS).exists()


def test_wrong_order_rejected_before_start(tmp_path: Path, runtime_wheels: Path) -> None:
    """The preparer does not silently change an accepted dependency plan."""
    fixtures.write_peers(tmp_path, runtime_wheels, (peers.ALPHA, peers.BETA))
    host = fixtures.host(tmp_path)
    candidate = host.accept().proposal.candidate
    changed = candidate.model_copy(update={"packages": tuple(reversed(candidate.packages))})
    with pytest.raises(RuntimePreparationError, match="dependency order"):
        host.preparation.prepare_runtime(changed)
    assert not (tmp_path / ENVIRONMENTS).exists()


def test_bad_identity_rejected_before_start(tmp_path: Path, runtime_wheels: Path) -> None:
    """Every package is checked before a valid first package can start."""
    fixtures.write_peers(tmp_path, runtime_wheels, (peers.ALPHA, peers.BETA))
    host = fixtures.host(tmp_path)
    candidate = host.accept().proposal.candidate
    changed = candidate.packages[-1].model_copy(update={
        "extension_info": candidate.packages[-1].extension_info.model_copy(update={"package_version": "2.0.0"}),
    })
    with pytest.raises(ExtensionContractError):
        host.preparation.prepare_runtime(candidate.model_copy(update={
            "packages": (*candidate.packages[:-1], changed),
        }))
    assert not (tmp_path / ENVIRONMENTS).exists()
