# Copyright (c) 2026 Zhambyl Yermagambet
"""Resolve wheel-owned policy resources and exact tool pins."""

import hashlib
from importlib import metadata
from pathlib import Path

from packaging.requirements import Requirement

from baqylau_dev.models import ToolVersion

POLICY_ROOT = Path(__file__).with_name("policy")
POLICY_FILES = ("ruff.toml", "ruff-paths.toml", "mypy.ini", "flake8.ini", "requirements.txt")
ENCODING = "utf-8"


def policy_version() -> str:
    """Read the installed distribution version.

    Returns:
        The exact quality policy release.

    """
    return metadata.version("baqylau-dev")


def policy_text(name: str) -> str:
    """Read one explicitly named packaged policy resource.

    Returns:
        UTF-8 policy text.

    Raises:
        ValueError: If the name does not identify a policy resource.

    """
    if name not in POLICY_FILES:
        message = "unknown policy resource"
        raise ValueError(message)
    return (POLICY_ROOT / name).read_text(encoding=ENCODING)


def policy_digest() -> str:
    """Identify the release and all authoritative policy bytes.

    Returns:
        A SHA-256 digest that does not depend on the installation path.

    """
    digest = hashlib.sha256(policy_version().encode(ENCODING))
    for name in POLICY_FILES:
        digest.update(f"\0{name}\0{policy_text(name)}".encode(ENCODING))
    for path in sorted(POLICY_ROOT.parent.glob("*.py")):
        source = path.read_text(encoding=ENCODING)
        digest.update(f"\0{path.name}\0{source}".encode(ENCODING))
    return digest.hexdigest()


def tool_versions() -> tuple[ToolVersion, ...]:
    """Inspect the installed tools without invoking pip or a package installer.

    Returns:
        The required and installed version for every pinned tool.

    """
    requirements = tuple(
        Requirement(line) for line in policy_text("requirements.txt").splitlines() if line.strip()
    )
    return tuple(ToolVersion(
        name=requirement.name, required=str(requirement.specifier), installed=_installed(requirement.name),
    ) for requirement in requirements)


def require_tool_versions() -> None:
    """Reject missing or changed tool versions before a gate starts.

    Raises:
        ValueError: If an installed version differs from the exact pin.

    """
    differences = tuple(
        f"{tool.name}: expected {tool.required}, installed {tool.installed}"
        for tool in tool_versions() if tool.required != f"=={tool.installed}"
    )
    if differences:
        raise ValueError("\n".join(differences))


def _installed(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "missing"
