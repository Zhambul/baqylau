# Copyright (c) 2026 Zhambyl Yermagambet
"""Restore private workers through actual application startup and the engine boundary."""

import os
from contextlib import closing
from pathlib import Path

import pytest

from extensions.runtime_ownership import FilesystemRuntimeOwnership
from extensions.runtime_ownership_contract import RuntimeBusyError
from tests.extension_host import manager_process_fixture as fixture, process_fixture


def test_daemon_restores_and_closes_worker(tmp_path: Path, runtime_wheels: Path) -> None:
    """The real daemon owns a fresh worker and closes it before releasing its lease."""
    previous = fixture.installed_peer(tmp_path, runtime_wheels)
    with process_fixture.running_catalog(tmp_path) as client:
        selected = fixture.replacement_worker(tmp_path, previous)
        assert selected not in {os.getpid(), client.application.wait_until_ready().process_id}
        with pytest.raises(RuntimeBusyError):
            FilesystemRuntimeOwnership(tmp_path).acquire_runtime()
        assert client.extensions.catalog().entries[0].issue is None
    fixture.require_stopped(tmp_path, selected)
    with closing(FilesystemRuntimeOwnership(tmp_path).acquire_runtime()):
        assert selected != previous


def test_daemon_restores_without_source_package(tmp_path: Path, runtime_wheels: Path) -> None:
    """Stored activation selects fixed artifacts even when discovery loses its source."""
    previous = fixture.installed_peer(tmp_path, runtime_wheels)
    (tmp_path / "packages").rename(tmp_path / "removed-source")
    with process_fixture.running_catalog(tmp_path) as client:
        selected = fixture.replacement_worker(tmp_path, previous)
        assert not client.extensions.catalog().entries
    fixture.require_stopped(tmp_path, selected)
    with process_fixture.running_catalog(tmp_path):
        restarted = fixture.replacement_worker(tmp_path, selected)
    fixture.require_stopped(tmp_path, restarted)
