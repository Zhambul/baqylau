# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject changed tools and unmanaged local rule overrides."""

import tomllib
from pathlib import Path

from baqylau_dev.configuration import HOST_RUFF_RESOURCE, expected_files
from baqylau_dev.models import ProjectProfile
from baqylau_dev.resources import policy_text, require_tool_versions

ENCODING = "utf-8"


def check_parity(root: Path, profile: ProjectProfile) -> None:
    """Check versions and policy inputs before running a quality gate."""
    require_tool_versions()
    if profile.profile == "host":
        _check_host_files(root, profile)
    else:
        _reject_local_rules(root)


def write_configuration(root: Path, profile: ProjectProfile) -> None:
    """Write generated tool files only after explicit generation or gate preparation.

    Raises:
        ValueError: If a generated file is linked outside the project.

    """
    for path, content in expected_files(root, profile):
        if not path.resolve().is_relative_to(root.resolve()):
            message = f"generated configuration escapes the project: {path}"
            raise ValueError(message)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding=ENCODING)


def _check_host_files(root: Path, profile: ProjectProfile) -> None:
    if (root / HOST_RUFF_RESOURCE).read_text(encoding=ENCODING) != policy_text("ruff.toml"):
        message = "installed Ruff policy differs from the host policy source"
        raise ValueError(message)
    for path, content in expected_files(root, profile):
        if not path.is_file() or path.read_text(encoding=ENCODING) != content:
            message = f"generated configuration drift: {path.name}; run make policy-generate"
            raise ValueError(message)


def _reject_local_rules(root: Path) -> None:
    for name in ("ruff.toml", ".ruff.toml", "mypy.ini", ".mypy.ini", ".flake8", "setup.cfg"):
        if (root / name).exists():
            message = f"local quality configuration is not permitted: {name}"
            raise ValueError(message)
    project = root / "pyproject.toml"
    if project.is_file():
        _check_project_tools(project)


def _check_project_tools(project: Path) -> None:
    document = tomllib.loads(project.read_text(encoding=ENCODING))
    tools = document.get("tool", {})
    if any(name in tools for name in ("ruff", "mypy", "flake8", "vulture")):
        message = "pyproject.toml cannot override the shared Python quality rules"
        raise ValueError(message)
