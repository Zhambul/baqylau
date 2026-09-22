# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject reports that select the daemon, a different SDK, or an outside SDK path."""

import platform
from importlib import metadata
from pathlib import Path

import pytest
from baqylau_extension_api.runtime.environment_probe import EnvironmentReport

from extensions.environment_identity import validate_environment_identity

EXECUTABLE_PATH = Path("bin/python")


@pytest.mark.parametrize("field", ["executable", "prefix", "base_prefix", "sdk_directory"])
def test_identity_requires_private_paths(tmp_path: Path, field: str) -> None:
    """Every reported path must retain the host-selected environment boundary."""
    report = _report(tmp_path)
    path = tmp_path if field == "base_prefix" else tmp_path.parent
    changed = report.model_copy(update={field: str(path)})
    with pytest.raises(ValueError, match=r"identity|outside"):
        validate_environment_identity(tmp_path / EXECUTABLE_PATH, changed)


@pytest.mark.parametrize("field", ["python_version", "sdk_version"])
def test_identity_requires_selected_versions(tmp_path: Path, field: str) -> None:
    """A compatible-looking but different SDK or Python cannot pass preparation."""
    report = _report(tmp_path).model_copy(update={field: "0.0.0"})
    with pytest.raises(ValueError, match="identity"):
        validate_environment_identity(tmp_path / EXECUTABLE_PATH, report)


def test_identity_accepts_selected_install(tmp_path: Path) -> None:
    """Matching installed paths and versions can proceed to worker launch."""
    validate_environment_identity(tmp_path / EXECUTABLE_PATH, _report(tmp_path))


def _report(directory: Path) -> EnvironmentReport:
    return EnvironmentReport(
        executable=str(directory / EXECUTABLE_PATH), prefix=str(directory),
        base_prefix=str(directory.parent), python_version=platform.python_version(),
        sdk_version=metadata.version("baqylau-extension-api"), sdk_directory=str(directory / "lib" / "sdk"),
    )
