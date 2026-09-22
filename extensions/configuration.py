# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep package roots explicit and separate from private worker environments."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ROOTS_ENVIRONMENT = "BAQYLAU_EXTENSION_ROOTS"
READ_ONLY_ENVIRONMENT = "BAQYLAU_EXTENSION_READ_ONLY"


@dataclass(frozen=True)
class ExtensionRoots:
    """Select directories whose immediate children are built package folders."""

    directories: tuple[Path, ...]


def configured_roots(environment: Mapping[str, str], data_directory: Path) -> ExtensionRoots:
    """Use the private data directory unless explicit roots were configured.

    Returns:
        Normalized roots; an explicit empty value disables discovery.

    """
    configured = environment.get(ROOTS_ENVIRONMENT)
    directories = (data_directory / "extensions",) if configured is None else tuple(
        Path(path) for path in configured.split(os.pathsep) if path
    )
    normalized = {path.expanduser().absolute() for path in directories}
    return ExtensionRoots(tuple(sorted(normalized)))


def roots_environment(directories: tuple[Path, ...]) -> str:
    """Encode explicit paths without changing the selected roots in child processes.

    Returns:
        A path-separated environment value.

    Raises:
        ValueError: If a path itself contains the environment separator.

    """
    paths = tuple(str(path.expanduser().absolute()) for path in directories)
    if any(os.pathsep in path for path in paths):
        message = "an extension root cannot contain the environment path separator"
        raise ValueError(message)
    return os.pathsep.join(paths)


def configured_read_only(environment: Mapping[str, str]) -> bool:
    """Read the explicit extension-management policy without ambiguous flag values.

    Returns:
        The host-selected read-only value, defaulting to normal management access.

    Raises:
        ValueError: If the configured value is neither 0 nor 1.

    """
    selected = environment.get(READ_ONLY_ENVIRONMENT, "0")
    if selected not in {"0", "1"}:
        message = "BAQYLAU_EXTENSION_READ_ONLY must be 0 or 1"
        raise ValueError(message)
    return selected == "1"
