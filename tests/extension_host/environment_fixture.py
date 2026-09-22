# Copyright (c) 2026 Zhambyl Yermagambet
"""Create captured external packages with complete locked test dependencies."""

import hashlib
import shutil
from pathlib import Path

from baqylau_extension_api.manifest.metadata import BackendEnvironment
from packaging.utils import parse_wheel_filename

from extensions.environments import LocalExtensionEnvironments
from extensions.models.artifacts import PackageArtifact
from extensions.preparation_runner import BoundedPreparationRunner
from tests.extension_host import artifact_fixture, package_fixture

LOCK_NAME = "requirements.lock"
WHEELHOUSE = "wheels"


def write_package(directory: Path, wheels: Path) -> Path:
    """Give an external fixture its own wheel bytes and pinned hash requirements.

    Returns:
        The complete source package, before capture or imports.

    """
    package = package_fixture.write_package(directory / "packages")
    shutil.copytree(wheels, package / WHEELHOUSE)
    (package / LOCK_NAME).write_text(locked_requirements(wheels), encoding="utf-8")
    manifest = package_fixture.read_manifest(package)
    assert manifest.backend is not None
    backend = manifest.backend.model_copy(update={
        "environment": BackendEnvironment(requirements=LOCK_NAME, wheelhouse=WHEELHOUSE),
    })
    package_fixture.save_manifest(package, manifest.model_copy(update={"backend": backend}))
    return package


def locked_requirements(wheels: Path) -> str:
    """Name each actual wheel version and its complete SHA-256 hash.

    Returns:
        A standard requirements lock for uv's hash check.

    """
    paths = sorted(wheels.glob("*.whl"))
    lines = "\n".join(_locked_line(path) for path in paths)
    return f"{lines}\n"


def prepare_store(directory: Path, source: Path) -> tuple[LocalExtensionEnvironments, PackageArtifact]:
    """Use production capture, storage, environment preparation, and command execution.

    Returns:
        The real preparation service and its selected checked artifact.

    """
    artifacts = artifact_fixture.store(directory)
    artifact = artifacts.capture_package(artifact_fixture.capture_request(source))
    service = LocalExtensionEnvironments(directory / "environments", artifacts, BoundedPreparationRunner())
    return service, artifact


def _locked_line(path: Path) -> str:
    name, version, _, _ = parse_wheel_filename(path.name)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"{name}=={version} --hash=sha256:{digest}"
