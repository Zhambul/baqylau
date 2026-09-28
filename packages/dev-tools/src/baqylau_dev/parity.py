# Copyright (c) 2026 Zhambyl Yermagambet
"""Reject changed tools and unmanaged local rule overrides."""

import tomllib
from collections.abc import Iterator
from pathlib import Path

from baqylau_dev.configuration import HOST_RUFF_RESOURCE, expected_files
from baqylau_dev.models import ProjectProfile
from baqylau_dev.resources import policy_text, require_tool_versions

ENCODING = "utf-8"
LOCAL_RULE_FILES = ("ruff.toml", ".ruff.toml", "mypy.ini", ".mypy.ini", ".flake8", "setup.cfg")
# Installed, built, and generated folders are not package source.
SKIPPED_FOLDERS = frozenset(("node_modules", "build", "dist", "wheels", "venv"))


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
    # The tools read the nearest configuration file, so a nested file would change the rules of its folder.
    for name in LOCAL_RULE_FILES:
        found = next(_package_files(root, name), None)
        if found is not None:
            message = f"local quality configuration is not permitted: {found.relative_to(root)}"
            raise ValueError(message)
    for project in _package_files(root, "pyproject.toml"):
        _check_project_tools(project)


def _package_files(root: Path, name: str) -> Iterator[Path]:
    for path in sorted(root.rglob(name)):
        folders = path.relative_to(root).parts[:-1]
        if not any(map(_skipped, folders)):
            yield path


def _skipped(folder: str) -> bool:
    return folder.startswith(".") or folder in SKIPPED_FOLDERS


def _check_project_tools(project: Path) -> None:
    document = tomllib.loads(project.read_text(encoding=ENCODING))
    tools = document.get("tool", {})
    if any(name in tools for name in ("ruff", "mypy", "flake8", "vulture")):
        message = "pyproject.toml cannot override the shared Python quality rules"
        raise ValueError(message)
