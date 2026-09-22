# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject incomplete wheel installs and release each failed preparation directory."""

from pathlib import Path

import pytest

from tests.extension_host import environment_fixture as environment, package_fixture

SHA256_LENGTH = 64
WRONG_HASH = "0" * SHA256_LENGTH


@pytest.mark.parametrize("mode", ["bad_hash", "missing_wheel", "missing_dependency", "missing_sdk"])
def test_failed_install_releases_owned_directory(tmp_path: Path, runtime_wheels: Path, mode: str) -> None:
    """Real uv installs cannot return a lease with missing or altered dependency bytes."""
    source = environment.write_package(tmp_path, runtime_wheels)
    _break_lock(source, mode)
    service, artifact = environment.prepare_store(tmp_path, source)
    with pytest.raises(ValueError, match="preparation failed"):
        service.prepare_environment(artifact.package_digest)
    assert not tuple(service.root.iterdir())
    assert Path(artifact.directory).is_dir()
    assert source.is_dir()


def test_missing_runtime_leaves_no_storage(tmp_path: Path) -> None:
    """Older prototype metadata remains discoverable but cannot use host dependencies."""
    source = package_fixture.write_package(tmp_path / "packages")
    service, artifact = environment.prepare_store(tmp_path, source)
    with pytest.raises(ValueError, match="locked runtime"):
        service.prepare_environment(artifact.package_digest)
    assert not service.root.exists()


def _break_lock(source: Path, mode: str) -> None:
    lock = source / environment.LOCK_NAME
    lines = lock.read_text(encoding="utf-8").splitlines()
    if mode == "bad_hash":
        pinned = lines[0].split("--hash=")[0]
        lock.write_text(f"{pinned}--hash=sha256:{WRONG_HASH}\n", encoding="utf-8")
    elif mode == "missing_wheel":
        _remove_wheel(source)
    else:
        prefix = "baqylau-extension-api" if mode == "missing_sdk" else "pydantic-core"
        retained = "\n".join(line for line in lines if not line.startswith(prefix))
        lock.write_text(f"{retained}\n", encoding="utf-8")


def _remove_wheel(source: Path) -> None:
    wheel = next((source / environment.WHEELHOUSE).glob("annotated_types-*.whl"))
    wheel.unlink()
