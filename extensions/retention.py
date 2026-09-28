# Copyright (c) 2026 Zhambyl Yermagambet
"""Remove package copies and worker environments that no stored state refers to."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from extensions.artifact_files import remove_sealed
from repository.contract.extension_retention import RetainedDigests

STAGING_PREFIX = ".capture-"
ENVIRONMENT_PREFIX = "environment-"


@dataclass(frozen=True)
class StartupRetention:
    """Run only while the manager holds the exclusive runtime lease, before any preparation."""

    artifacts: Path
    environments: Path
    digests: RetainedDigests

    def collect(self) -> None:
        """Remove unreferred copies, interrupted captures, and environments left by a stopped daemon."""
        retained = self.digests.retained_digests()
        copies = tuple(
            path for path in _children(self.artifacts)
            if path.name.startswith(STAGING_PREFIX) or path.name not in retained
        )
        for copy in copies:
            remove_sealed(copy)
        environments = tuple(
            path for path in _children(self.environments) if path.name.startswith(ENVIRONMENT_PREFIX)
        )
        for environment in environments:
            shutil.rmtree(environment)


def _children(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    return tuple(path for path in root.iterdir() if _real_directory(path))


def _real_directory(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()
