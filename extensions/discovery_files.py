# Copyright (c) 2026 Zhambyl Yermagambet
"""Read a bounded package inventory with no feature imports or followed file links."""

import hashlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from extensions.discovery_bytes import read_file

MAX_PACKAGE_FILES = 10_000
MAX_PACKAGE_BYTES = 268_435_456


@dataclass(frozen=True)
class PackageFile:
    """Record the bytes and executable bits of one package-owned regular file."""

    relative_path: str
    byte_length: int
    digest: str
    executable_bits: int


def package_files(directory: Path) -> tuple[PackageFile, ...]:
    """Hash all files in a built package, including tests and backend resources.

    Returns:
        The complete ordered file inventory.

    """
    inventory = []
    total = 0
    for path in _walk_files(directory):
        captured = read_file(path, MAX_PACKAGE_BYTES - total)
        entry = PackageFile(
            path.relative_to(directory).as_posix(), captured.byte_length, captured.digest, captured.executable_bits,
        )
        inventory.append(entry)
        total += entry.byte_length
    return tuple(sorted(inventory, key=lambda entry: entry.relative_path))


def inventory_digest(inventory: tuple[PackageFile, ...]) -> str:
    """Bind paths, lengths, file bytes, and executable bits to one digest.

    Returns:
        A location-independent SHA-256 digest for this exact inventory.

    """
    digest = hashlib.sha256(b"baqylau-package:v1\0")
    for entry in inventory:
        fields = (
            entry.relative_path,
            str(entry.byte_length),
            entry.digest,
            str(entry.executable_bits),
        )
        for field in fields:
            encoded = field.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return digest.hexdigest()


def _walk_files(directory: Path) -> Iterator[Path]:
    count = 0
    for parent, directories, filenames in os.walk(directory, followlinks=False, onerror=_raise_walk_error):
        count += len(directories) + len(filenames)
        if count > MAX_PACKAGE_FILES:
            message = "package inventory exceeds its path limit"
            raise ValueError(message)
        if any((Path(parent) / name).is_symlink() for name in directories):
            message = "package directories cannot be internal links"
            raise ValueError(message)
        yield from (Path(parent) / name for name in sorted(filenames))


def _raise_walk_error(error: OSError) -> None:
    raise error
