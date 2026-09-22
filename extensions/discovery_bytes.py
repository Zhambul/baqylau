# Copyright (c) 2026 Zhambyl Yermagambet
"""Hash bounded regular package files without following the final file link."""

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from extensions.models.files import FileBytes

READ_BYTES = 65_536
EXECUTABLE_BITS = 0o111
READ_ONLY_FILE = 0o444


def read_document(path: Path, limit: int) -> bytes:
    """Read bounded manifest bytes from a regular non-linked file.

    Returns:
        The exact captured bytes.

    Raises:
        ValueError: If the document exceeds its byte limit.

    """
    with _open_file(path) as source:
        encoded = source.read(limit + 1)
    if len(encoded) > limit:
        message = "package document exceeds its byte limit"
        raise ValueError(message)
    return encoded


def read_file(path: Path, limit: int) -> FileBytes:
    """Read only a regular file and detect changes during its bounded read.

    Returns:
        The captured byte identity.

    """
    with _open_file(path) as source:
        return _checked_read(source, limit)


def capture_file(source_path: Path, destination: Path, file_bytes: FileBytes) -> None:
    """Create one checked independent file without following links or accepting growth.

    Raises:
        ValueError: If the captured source does not match the selected inventory.

    """
    with _open_file(source_path) as source, destination.open("xb") as output:
        if _checked_read(source, file_bytes.byte_length, output) != file_bytes:
            message = "package file changed before capture"
            raise ValueError(message)
        output.flush()
        os.fsync(output.fileno())
    destination.chmod(READ_ONLY_FILE | file_bytes.executable_bits)


def _checked_read(source: BinaryIO, limit: int, output: BinaryIO | None = None) -> FileBytes:
    before = os.fstat(source.fileno())
    hashed = _bounded_digest(source, limit, output)
    after = os.fstat(source.fileno())
    _require_unchanged(before, after, hashed[1])
    executable = stat.S_IMODE(after.st_mode) & EXECUTABLE_BITS
    return FileBytes(hashed[0], hashed[1], executable)


@contextmanager
def _open_file(path: Path) -> Iterator[BinaryIO]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            message = "package files must be regular files"
            raise ValueError(message)
        yield source


def _require_unchanged(before: os.stat_result, after: os.stat_result, size: int) -> None:
    changed = (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
    if changed or size != after.st_size or before.st_mode != after.st_mode:
        message = "package file changed during discovery"
        raise ValueError(message)


def _bounded_digest(source: BinaryIO, limit: int, output: BinaryIO | None = None) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(READ_BYTES):
        size += len(chunk)
        if size > limit:
            message = "package bytes exceed the inventory limit"
            raise ValueError(message)
        digest.update(chunk)
        if output is not None:
            output.write(chunk)
    return digest.hexdigest(), size
