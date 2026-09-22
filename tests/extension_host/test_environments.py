# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare real private Python environments from separate captured packages."""

from contextlib import closing
from dataclasses import replace
from pathlib import Path

from baqylau_extension_api.runtime.environment_probe import EnvironmentReport

from extensions.environment_contract import WorkerEnvironment
from extensions.preparation_runner import BoundedPreparationRunner
from tests.extension_host import environment_fixture as environment, package_fixture, preparation_fixture


def test_private_environment_uses_installed_sdk(tmp_path: Path, runtime_wheels: Path) -> None:
    """The SDK runs in a new environment with no main repository import path."""
    source = environment.write_package(tmp_path, runtime_wheels)
    service, artifact = environment.prepare_store(tmp_path, source)
    with closing(service.prepare_environment(artifact.package_digest)) as lease:
        report = _probe(lease, tmp_path)
        assert Path(report.prefix) == lease.executable.parent.parent
        assert Path(report.sdk_directory).is_relative_to(Path(report.prefix))
        assert lease.artifact == artifact
        assert not (source / "uninstalled_feature" / package_fixture.MARKER_NAME).exists()
    assert not lease.executable.parents[2].exists()


def _probe(lease: WorkerEnvironment, directory: Path) -> EnvironmentReport:
    command = preparation_fixture.command(directory, "pass")
    arguments = (str(lease.executable), "-I", "-B", "-m", EnvironmentReport.__module__)
    output = BoundedPreparationRunner().run_preparation(replace(command, arguments=arguments))
    return EnvironmentReport.model_validate_json(output.stdout)


def test_preparation_ignores_changed_source(tmp_path: Path, runtime_wheels: Path) -> None:
    """Mutable package files cannot change the selected SDK dependencies."""
    source = environment.write_package(tmp_path, runtime_wheels)
    service, artifact = environment.prepare_store(tmp_path, source)
    (source / environment.LOCK_NAME).write_text("-r /outside-the-package", encoding="utf-8")
    with closing(service.prepare_environment(artifact.package_digest)) as lease:
        assert _probe(lease, tmp_path).sdk_version == "0.1.0a1"


def test_environment_ownership_is_separate(tmp_path: Path, runtime_wheels: Path) -> None:
    """Closing one generation cannot remove another or the captured package."""
    source = environment.write_package(tmp_path, runtime_wheels)
    service, artifact = environment.prepare_store(tmp_path, source)
    with (
        closing(service.prepare_environment(artifact.package_digest)) as first,
        closing(service.prepare_environment(artifact.package_digest)) as second,
    ):
        assert first.executable != second.executable
        first.close()
        assert _probe(second, tmp_path).sdk_version == "0.1.0a1"
        assert Path(artifact.directory).is_dir()


def test_worker_python_cannot_import_the_host(tmp_path: Path, runtime_wheels: Path) -> None:
    """The private Python has no inherited host or mutable feature source path."""
    source = environment.write_package(tmp_path, runtime_wheels)
    service, artifact = environment.prepare_store(tmp_path, source)
    with closing(service.prepare_environment(artifact.package_digest)) as lease:
        _check_host_absent(lease, tmp_path)


def _check_host_absent(lease: WorkerEnvironment, directory: Path) -> None:
    source = (
        "import importlib.util; "
        "assert all(importlib.util.find_spec(name) is None "
        "for name in ('domain', 'extensions', 'uninstalled_feature'))"
    )
    command = preparation_fixture.command(directory, source)
    arguments = (str(lease.executable), *command.arguments[1:])
    output = BoundedPreparationRunner().run_preparation(replace(command, arguments=arguments))
    assert output.return_code == 0, output.stderr.decode()
