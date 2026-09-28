# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the runtime distributions that a package's worker needs: the SDK's and the package's own."""

from __future__ import annotations

import tomllib
from importlib import metadata
from typing import TYPE_CHECKING

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

if TYPE_CHECKING:
    from pathlib import Path

SDK = "baqylau-extension-api"


def worker_distributions(package: Path) -> set[str]:
    """Name every distribution that the package's worker needs, except the SDK, whose wheel is the built one.

    Returns:
        The SDK's dependencies and the package's own runtime dependencies, with their dependencies.

    """
    return dependency_names(SDK, *package_requirements(package))


def dependency_names(*roots: str) -> set[str]:
    """Follow the installed root distributions' mandatory dependencies.

    Returns:
        Every direct and transitive dependency name, without the SDK, whose wheel is the built one.

    """
    selected: set[str] = set()
    pending = list(roots)
    while pending:
        name = canonicalize_name(pending.pop())
        if name not in selected:
            selected.add(name)
            pending.extend(_required(metadata.distribution(name)))
    return selected - {SDK}


def package_requirements(package: Path) -> tuple[str, ...]:
    """Name the runtime dependencies that the package's `pyproject.toml` declares.

    Returns:
        The distribution names; none when the package has no `pyproject.toml`.

    """
    pyproject = package / "pyproject.toml"
    if not pyproject.is_file():
        return ()
    project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
    parsed = (Requirement(line) for line in project.get("dependencies", ()))
    return tuple(canonicalize_name(requirement.name) for requirement in parsed)


def _required(distribution: metadata.Distribution) -> tuple[str, ...]:
    parsed = (Requirement(line) for line in distribution.requires or ())
    return tuple(
        canonicalize_name(requirement.name) for requirement in parsed
        if requirement.marker is None or requirement.marker.evaluate({"extra": ""})
    )
