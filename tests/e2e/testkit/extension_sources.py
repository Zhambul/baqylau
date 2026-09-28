# Copyright (c) 2026 Zhambyl Yermagambet
"""Name the extension packages that the live scenarios install, and find their sources."""

from __future__ import annotations

import os
import subprocess  # noqa: S404 -- Ask Git for the main checkout of this worktree.
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PACKAGES_VARIABLE = "BAQYLAU_E2E_PACKAGES_DIR"


@dataclass(frozen=True)
class PackageSource:
    """Name one package and the directory that holds its source."""

    owner: str
    directory: str
    beside_checkout: bool


PACKAGES = MappingProxyType({
    "hello": PackageSource("example.hello", "examples/hello-extension", beside_checkout=False),
    "adapters": PackageSource("baqylau.adapters", "baqylau-adapters", beside_checkout=True),
    "git": PackageSource("baqylau.git", "baqylau-git", beside_checkout=True),
})


def source_directory(source: PackageSource) -> Path:
    """Find a package's source; an external package is beside the main checkout unless the variable names a place.

    Returns:
        The source directory.

    Raises:
        FileNotFoundError: If the source is not there.

    """
    if not source.beside_checkout:
        return REPOSITORY_ROOT / source.directory
    configured = os.environ.get(PACKAGES_VARIABLE)
    home = Path(configured) if configured else _main_checkout().parent
    directory = home / source.directory
    if not (directory / "extension.json").is_file():
        message = f"the {source.owner} package is not in {directory}; set {PACKAGES_VARIABLE}"
        raise FileNotFoundError(message)
    return directory


def _main_checkout() -> Path:
    common = subprocess.run(
        ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"),  # noqa: S607 -- Git is on the PATH.
        cwd=REPOSITORY_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return Path(common).parent
