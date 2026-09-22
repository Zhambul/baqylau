# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare dependency environments from fixed packages and own their cleanup."""

from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from baqylau_extension_api.runtime.environment_probe import EnvironmentReport

from extensions.artifact_contract import ExtensionArtifacts
from extensions.environment_commands import EnvironmentCommands
from extensions.environment_contract import ExtensionEnvironments, PreparationRunner, WorkerEnvironment
from extensions.environment_identity import validate_environment_identity
from extensions.environment_requirements import validate_runtime_requirements
from extensions.models.artifacts import PackageArtifact
from extensions.models.processes import PreparationCommand, PreparationOutput

PRIVATE_DIRECTORY = 0o700


@dataclass(frozen=True)
class ManagedWorkerEnvironment(WorkerEnvironment):
    """Keep the original environment path alive until all its worker work is released."""

    _artifact: PackageArtifact
    _executable: Path
    _owned: TemporaryDirectory[str]

    @property
    def artifact(self) -> PackageArtifact:
        """The selected fixed package."""
        return self._artifact

    @property
    def executable(self) -> Path:
        """The selected private Python path."""
        return self._executable

    def close(self) -> None:
        """Remove only this operation's owned environment directory."""
        self._owned.cleanup()


@dataclass(frozen=True)
class LocalExtensionEnvironments(ExtensionEnvironments):
    """Use a new generation for every preparation; never mutate an active environment."""

    root: Path
    artifacts: ExtensionArtifacts
    runner: PreparationRunner

    def prepare_environment(self, package_digest: str) -> WorkerEnvironment:
        """Check the artifact and build an isolated dependency set before returning ownership.

        Returns:
            A checked environment whose owner must close it after its workers stop.

        """
        artifact = self.artifacts.read_artifact(package_digest)
        validate_runtime_requirements(_require_environment(artifact)[0])
        root = _environment_root(self.root, Path(artifact.directory))
        with ExitStack() as cleanup:
            owned = TemporaryDirectory(prefix="environment-", dir=root)
            cleanup.callback(owned.cleanup)
            commands = EnvironmentCommands(Path(owned.name), Path(artifact.directory))
            self._prepare(artifact, commands)
            cleanup.pop_all()
            return ManagedWorkerEnvironment(artifact, commands.executable, owned)

    def _prepare(self, artifact: PackageArtifact, commands: EnvironmentCommands) -> None:
        requirements, wheelhouse = _require_environment(artifact)
        _run_checked(self.runner, commands.create_command())
        _run_checked(self.runner, commands.sync_command(requirements, wheelhouse))
        _run_checked(self.runner, commands.check_command())
        output = _run_checked(self.runner, commands.probe_command())
        validate_environment_identity(commands.executable, EnvironmentReport.model_validate_json(output.stdout))


def _environment_root(root: Path, artifact: Path) -> Path:
    normalized = root.expanduser().absolute()
    if normalized.is_symlink() or normalized.resolve().is_relative_to(artifact):
        message = "private environments require separate non-linked storage"
        raise ValueError(message)
    normalized.mkdir(mode=PRIVATE_DIRECTORY, parents=True, exist_ok=True)
    return normalized.resolve()


def _require_environment(artifact: PackageArtifact) -> tuple[Path, Path]:
    backend = artifact.manifest.backend
    if backend is None or backend.environment is None:
        message = "backend activation requires declared locked runtime dependencies"
        raise ValueError(message)
    directory = Path(artifact.directory)
    requirements = directory / backend.environment.requirements
    wheelhouse = directory / backend.environment.wheelhouse
    if not requirements.is_file() or not wheelhouse.is_dir():
        message = "the captured runtime requirements or wheelhouse are absent"
        raise ValueError(message)
    return requirements, wheelhouse


def _run_checked(runner: PreparationRunner, command: PreparationCommand) -> PreparationOutput:
    output = runner.run_preparation(command)
    if output.return_code != 0:
        message = "extension dependency preparation failed; inspect the package lock and wheels"
        raise ValueError(message)
    return output
