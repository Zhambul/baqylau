# Copyright (c) 2026 Zhambyl Yermagambet
"""Install extension packages into the live application, and disable them again.

The live application reads packages from `<data directory>/extensions`. A
scenario copies a package there, builds its environment, rescans the catalog,
and enables it; the scenario's end disables it again, so the other scenarios of
the worker do not see it.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from baqylau_extension_testkit.client import HostClient
from baqylau_extension_testkit.lifecycle import change, rescan
from baqylau_extension_testkit.wheelhouse import build_environment

from tests.e2e.testkit.extension_sources import PACKAGES, source_directory
from tests.e2e.testkit.process import ApplicationProcess

if TYPE_CHECKING:
    from baqylau_extension_testkit.lifecycle_models import LifecycleAction

BUILT = shutil.ignore_patterns(
    ".git", ".baqylau-dev", "wheels", "requirements.lock", "__pycache__", "*.egg-info", "build", "node_modules",
    ".mypy_cache", ".ruff_cache", ".pytest_cache",
)


@dataclass
class ExtensionPackages:
    """Install packages into one live application, and disable them at the scenario's end."""

    process: ApplicationProcess
    sdk_wheel: Path
    enabled: list[str] = field(default_factory=list)

    @property
    def client(self) -> HostClient:
        """A client for the application's extension routes."""
        return HostClient(self.process.endpoint.url)

    def enable(self, name: str) -> str:
        """Copy, build, rescan, and enable one package.

        Returns:
            The package owner.

        """
        source = PACKAGES[name]
        target = self.process.config.data_directory / "extensions" / source.owner
        if not target.exists():
            shutil.copytree(source_directory(source), target, ignore=BUILT)
            build_environment(target, self.sdk_wheel)
        rescan(self.client)
        self._change(source.owner, "enable")
        self.enabled.append(source.owner)
        return source.owner

    def disable(self, name: str) -> None:
        """Disable one package this scenario enabled."""
        owner = PACKAGES[name].owner
        self._change(owner, "disable")
        self.enabled.remove(owner)

    def disable_all(self) -> None:
        """Disable every package this scenario enabled, the newest first."""
        for owner in reversed(self.enabled):
            self._change(owner, "disable")
        self.enabled.clear()

    def _change(self, owner: str, action: LifecycleAction) -> None:
        unique = uuid.uuid4().hex
        operation = change(self.client, owner, action, f"e2e-{action}-{owner}-{unique}")
        assert operation.status == "succeeded", operation
