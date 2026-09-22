# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep private environment creation and subprocess execution behind protocols."""

from pathlib import Path
from typing import Protocol

from extensions.models.artifacts import PackageArtifact
from extensions.models.processes import PreparationCommand, PreparationOutput


class PreparationRunner(Protocol):
    """Run a bounded host-owned preparation command outside the daemon process."""

    def run_preparation(self, preparation_command: PreparationCommand) -> PreparationOutput:
        """Run the selected command or fail after releasing its process group."""
        ...


class WorkerEnvironment(Protocol):
    """Own one checked private environment at its original non-moved path."""

    @property
    def artifact(self) -> PackageArtifact:
        """The captured feature package bound to this environment."""
        ...

    @property
    def executable(self) -> Path:
        """The environment's Python executable, not the daemon interpreter."""
        ...

    def close(self) -> None:
        """Release only this owned environment after its workers stop."""
        ...


class ExtensionEnvironments(Protocol):
    """Prepare private runtime dependencies without changing active state."""

    def prepare_environment(self, package_digest: str) -> WorkerEnvironment:
        """Check the fixed artifact and prepare an owned dependency environment."""
        ...
