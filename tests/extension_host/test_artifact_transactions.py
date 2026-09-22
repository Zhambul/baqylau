# Copyright (c) 2026 Zhambyl Yermagambet
"""Test real file publication races and failure cleanup without active workers."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from extensions import artifact_files
from tests.extension_host import artifact_faults, artifact_fixture as artifacts, package_fixture as packages

SOURCE_DIRECTORY = "source"
CONCURRENT_CAPTURES = 2


def test_concurrent_captures_publish_one_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two complete copies converge without replacement or leaked staging paths."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    request = artifacts.capture_request(source)
    store = artifacts.store(tmp_path)
    monkeypatch.setattr(artifact_files, "publish_directory", artifact_faults.ConcurrentPublication(
        Barrier(CONCURRENT_CAPTURES), artifact_files.publish_directory,
    ))
    with ThreadPoolExecutor(max_workers=CONCURRENT_CAPTURES) as pool:
        captured = tuple(pool.map(store.capture_package, (request, request)))
    assert captured[0] == captured[1]
    assert store.read_artifact(request.expected_digest) == captured[0]
    assert {path.name for path in store.root.iterdir()} == {request.expected_digest}


@pytest.mark.parametrize("change", ["grow", "replace", "fifo"])
def test_change_during_copy_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    """Inventory validation cannot permit a later changed file to enter the copy."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    request = artifacts.capture_request(source)
    store = artifacts.store(tmp_path)
    monkeypatch.setattr(artifact_files, "capture_inventory", artifact_faults.ChangedCapture(
        change, artifact_files.capture_inventory,
    ))
    with pytest.raises(ValueError, match="package"):
        store.capture_package(request)
    assert not tuple(store.root.iterdir())


def test_failed_publish_cleans_read_only_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Failure after sealing leaves no partial public target or staging files."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    store = artifacts.store(tmp_path)
    monkeypatch.setattr(artifact_files, "publish_directory", artifact_faults.fail_publication)
    with pytest.raises(OSError, match="publication failure"):
        store.capture_package(artifacts.capture_request(source))
    assert not tuple(store.root.iterdir())


@pytest.mark.parametrize("kind", ["file", "directory", "link"])
def test_invalid_existing_target_is_preserved(tmp_path: Path, kind: str) -> None:
    """Capture reports a damaged path instead of overwriting its contents."""
    source = packages.write_package(tmp_path / SOURCE_DIRECTORY)
    store = artifacts.store(tmp_path)
    request = artifacts.capture_request(source)
    store.root.mkdir()
    target = store.root / request.expected_digest
    if kind == "file":
        target.write_bytes(b"invalid artifact")
    elif kind == "directory":
        target.mkdir()
    else:
        target.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match=r"(artifact|extension\.json)"):
        store.capture_package(request)
    assert target.exists()
    assert tuple(store.root.iterdir()) == (target,)
