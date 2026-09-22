# Copyright (c) 2026 Zhambyl Yermagambet
"""Inject file changes at the exact capture and publication boundaries."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier

from extensions.discovery_files import PackageFile
from tests.extension_host import package_fixture as packages

type CaptureInventory = Callable[[Path, Path, tuple[PackageFile, ...]], None]
type PublishDirectory = Callable[[Path, Path], None]


@dataclass(frozen=True)
class ConcurrentPublication:
    """Hold both complete candidate copies before either can become the winner."""

    barrier: Barrier
    publish: PublishDirectory

    def __call__(self, staged: Path, target: Path) -> None:
        """Let the real rename resolve publication after both captures are ready."""
        self.barrier.wait(timeout=5)
        self.publish(staged, target)


@dataclass(frozen=True)
class ChangedCapture:
    """Change source bytes after the whole inventory digest was checked."""

    change: str
    capture: CaptureInventory

    def __call__(self, source: Path, destination: Path, inventory: tuple[PackageFile, ...]) -> None:
        """Use the real bounded file copier against the changed source."""
        path = source / packages.BACKEND_PATH
        if self.change == "grow":
            path.write_bytes(packages.BACKEND_SOURCE.encode(packages.ENCODING) * 2)
        elif self.change == "replace":
            path.write_bytes(b"changed")
        else:
            path.rename(source / "saved.py")
            os.mkfifo(path)
        self.capture(source, destination, inventory)


def fail_publication(staged: Path, target: Path) -> None:
    """Fail only after the candidate is complete, without changing its target.

    Raises:
        OSError: To test removal of an owned read-only staging directory.

    """
    assert (staged / "extension.json").is_file()
    assert not target.exists()
    message = "test publication failure"
    raise OSError(message)
