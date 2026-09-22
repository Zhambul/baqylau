# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the lexical form of host paths without filesystem access."""

from pathlib import PurePosixPath
from typing import Annotated

from pydantic import AfterValidator

from baqylau_extension_api.models.base import NonemptyText


def absolute_path(path: str) -> str:
    """Require a normalized absolute path for a POSIX host.

    The host still must resolve symlinks and verify the repository identity.

    Returns:
        The unchanged absolute path.

    Raises:
        ValueError: If the path is relative or has an unsafe lexical form.

    """
    parsed = PurePosixPath(path)
    normalized = str(parsed) == path
    if not parsed.is_absolute() or ".." in parsed.parts or not normalized:
        message = "host path must be normalized and absolute"
        raise ValueError(message)
    if "\x00" in path or path.startswith("//"):
        message = "host path contains an invalid prefix or null character"
        raise ValueError(message)
    return path


AbsolutePath = Annotated[NonemptyText, AfterValidator(absolute_path)]


def relative_path(path: str) -> str:
    """Require one normalized package-relative POSIX file path.

    Returns:
        The unchanged path, with no URL or traversal syntax.

    Raises:
        ValueError: If the path can escape or name a package directory.

    """
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or str(parsed) != path or path == ".":
        message = "package path must be normalized and relative"
        raise ValueError(message)
    forbidden = ("\x00", "\\", ":", "%", "?", "#")
    if ".." in parsed.parts or any(character in path for character in forbidden):
        message = "package path contains traversal or URL syntax"
        raise ValueError(message)
    return path


RelativePath = Annotated[NonemptyText, AfterValidator(relative_path)]
