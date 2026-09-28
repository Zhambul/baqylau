# Copyright (c) 2026 Zhambyl Yermagambet
"""Copy only selected regular files and publish complete package directories."""

import errno
import os
import shutil
from contextlib import ExitStack
from pathlib import Path

from extensions.discovery_bytes import capture_file
from extensions.discovery_files import PackageFile
from extensions.models.files import FileBytes

READ_ONLY_DIRECTORY = 0o555
WRITABLE_DIRECTORY = 0o755


def capture_inventory(source: Path, destination: Path, inventory: tuple[PackageFile, ...]) -> None:
    """Copy each selected file into a new private package directory."""
    destination.mkdir(exist_ok=True)
    for entry in inventory:
        output = destination / entry.relative_path
        output.parent.mkdir(parents=True, exist_ok=True)
        captured = FileBytes(entry.digest, entry.byte_length, entry.executable_bits)
        capture_file(source / entry.relative_path, output, captured)


def seal_directory(directory: Path) -> None:
    """Remove normal write access and sync directories after their files are complete."""
    for parent, _, _ in os.walk(directory, topdown=False):
        path = Path(parent)
        path.chmod(READ_ONLY_DIRECTORY)
        _sync_directory(path)


def remove_sealed(directory: Path) -> None:
    """Give the owner write access to a sealed tree again, then delete it."""
    for parent, _, _ in os.walk(directory):
        Path(parent).chmod(WRITABLE_DIRECTORY)
    shutil.rmtree(directory)


def publish_directory(staged: Path, target: Path) -> None:
    """Publish a complete directory or leave an existing concurrent copy unchanged.

    Raises:
        OSError: If publication failed for a reason other than an existing target.

    """
    try:
        staged.rename(target)
    except OSError as error:
        # macOS can report EACCES before ENOTEMPTY for a read-only winner.
        # The caller must still verify the complete existing artifact.
        existing_target = error.errno in {errno.EEXIST, errno.ENOTEMPTY, errno.EACCES}
        if not existing_target or not target.is_dir():
            raise
    _sync_directory(target.parent)


def _sync_directory(directory: Path) -> None:
    with ExitStack() as cleanup:
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        cleanup.callback(os.close, descriptor)
        os.fsync(descriptor)
