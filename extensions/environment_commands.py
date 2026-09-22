# Copyright (c) 2026 Zhambyl Yermagambet
"""Build explicit offline uv commands for one new private environment."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from extensions.models.processes import PreparationCommand

UV_ARGUMENTS = ("--offline", "--no-config", "--no-cache", "--no-python-downloads", "--color", "never")


@dataclass(frozen=True)
class EnvironmentCommands:
    """Keep every installer target and process environment owned by the host."""

    directory: Path
    artifact_directory: Path
    python: str = sys.executable

    @property
    def executable(self) -> Path:
        """The private executable path, without resolving its interpreter link."""
        return self.directory / "venv" / "bin" / "python"

    def create_command(self) -> PreparationCommand:
        """Create a new environment at its final original path.

        Returns:
            A command that neither discovers projects nor downloads Python.

        """
        return self._uv_command((
            "venv", str(self.directory / "venv"), "--python", self.python, "--no-project", *UV_ARGUMENTS,
        ))

    def sync_command(self, requirements: Path, wheelhouse: Path) -> PreparationCommand:
        """Install only locked wheel dependencies in the selected environment.

        Returns:
            A hash-checked offline synchronization command.

        """
        return self._uv_command((
            "pip", "sync", str(requirements), "--python", str(self.executable),
            "--require-hashes", "--only-binary", ":all:", "--no-index",
            "--find-links", str(wheelhouse), "--link-mode", "copy", *UV_ARGUMENTS,
        ))

    def check_command(self) -> PreparationCommand:
        """Check complete dependency compatibility after synchronization.

        Returns:
            The installer's standard dependency check.

        """
        return self._uv_command(("pip", "check", "--python", str(self.executable), *UV_ARGUMENTS))

    def probe_command(self) -> PreparationCommand:
        """Inspect the installed SDK without adding source paths or importing the feature.

        Returns:
            A typed environment report from isolated Python.

        """
        return self._command((
            str(self.executable), "-I", "-B", "-m", "baqylau_extension_api.runtime.environment_probe",
        ))

    def _uv_command(self, arguments: tuple[str, ...]) -> PreparationCommand:
        return self._command((self.python, "-I", "-m", "uv", *arguments))

    def _command(self, arguments: tuple[str, ...]) -> PreparationCommand:
        environment = MappingProxyType({
            "PATH": os.defpath, "TMPDIR": str(self.directory), "LC_ALL": "C",
        })
        return PreparationCommand(arguments, environment, self.artifact_directory)
