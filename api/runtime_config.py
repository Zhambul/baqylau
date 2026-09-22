# Copyright (c) 2026 Zhambyl Yermagambet
"""Define process configuration independently of socket and server startup."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from extensions import configuration as extension_configuration
from harness.runtime import HarnessRuntimeConfigs, default_harness_runtime_configs

ENABLED_ENV_VALUE = "1"
DISABLED_ENV_VALUE = "0"


@dataclass(frozen=True)
class ApplicationConfig:
    """All process-level choices for one application run."""

    data_directory: Path
    host: str = "127.0.0.1"
    port: int = 8377
    terminal: str | None = None
    notify_telegram: bool = True
    notify_webpush: bool = True
    harness_runtime_configs: HarnessRuntimeConfigs = field(
        default_factory=default_harness_runtime_configs, repr=False, compare=False,
    )
    environment_removals: tuple[str, ...] = ()
    extension_roots: tuple[Path, ...] | None = None
    extension_read_only: bool = False
    base_environment: Mapping[str, str] = field(default_factory=lambda: dict(os.environ), repr=False, compare=False)

    @classmethod
    def from_environment(cls, harness_runtime_configs: HarnessRuntimeConfigs | None = None) -> ApplicationConfig:
        """Read the explicit environment before the application graph is built.

        Returns:
            Configuration for one application process.

        """
        environment = dict(os.environ)
        configured = environment.get("BAQYLAU_DATA_DIR") or environment.get("BAQYLAU_DATA_DIRECTORY")
        directory = Path(configured or "~/.local/share/baqylau").expanduser().resolve()
        try:
            port = int(environment.get("BAQYLAU_DASHBOARD_PORT", "8377"))
        except ValueError:
            port = 8377
        return cls(
            data_directory=directory, port=port, terminal=environment.get("BAQYLAU_TERMINAL"),
            notify_telegram=(
                environment.get("BAQYLAU_DASHBOARD_NOTIFY_TELEGRAM", ENABLED_ENV_VALUE) != DISABLED_ENV_VALUE
            ),
            notify_webpush=(
                environment.get("BAQYLAU_DASHBOARD_NOTIFY_WEBPUSH", ENABLED_ENV_VALUE) != DISABLED_ENV_VALUE
            ),
            harness_runtime_configs=harness_runtime_configs or default_harness_runtime_configs(),
            base_environment=environment,
            extension_roots=extension_configuration.configured_roots(environment, directory).directories,
            extension_read_only=extension_configuration.configured_read_only(environment),
        )

    def process_environment(self) -> Mapping[str, str]:
        """Build the environment used by the application and its children.

        Returns:
            Explicit paths and options without inherited conflicting values.

        """
        environment = dict(self.base_environment)
        for name in self.environment_removals:
            environment.pop(name, None)
        environment["BAQYLAU_DATA_DIR"] = str(self.data_directory)
        environment["BAQYLAU_DASHBOARD_PORT"] = str(self.port)
        roots = (self.data_directory / "extensions",) if self.extension_roots is None else self.extension_roots
        environment[extension_configuration.ROOTS_ENVIRONMENT] = extension_configuration.roots_environment(roots)
        environment[extension_configuration.READ_ONLY_ENVIRONMENT] = (
            ENABLED_ENV_VALUE if self.extension_read_only else DISABLED_ENV_VALUE
        )
        if self.terminal is None:
            environment.pop("BAQYLAU_TERMINAL", None)
        else:
            environment["BAQYLAU_TERMINAL"] = self.terminal
        environment["BAQYLAU_DASHBOARD_NOTIFY_TELEGRAM"] = (
            ENABLED_ENV_VALUE if self.notify_telegram else DISABLED_ENV_VALUE
        )
        environment["BAQYLAU_DASHBOARD_NOTIFY_WEBPUSH"] = (
            ENABLED_ENV_VALUE if self.notify_webpush else DISABLED_ENV_VALUE
        )
        return environment
