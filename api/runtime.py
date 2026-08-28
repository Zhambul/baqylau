"""The explicit configuration and runtime of one dashboard application."""

from __future__ import annotations

import os
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from audit.models import PortAudit
from harness.runtime import HarnessRuntimeConfigs, default_harness_runtime_configs


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
        default_factory=default_harness_runtime_configs,
        repr=False,
        compare=False,
    )
    environment_removals: tuple[str, ...] = ()
    base_environment: Mapping[str, str] = field(
        default_factory=lambda: dict(os.environ), repr=False, compare=False
    )

    @classmethod
    def from_environment(
        cls,
        harness_runtime_configs: HarnessRuntimeConfigs | None = None,
    ) -> ApplicationConfig:
        environment = dict(os.environ)
        configured_directory = (
            environment.get("BAQYLAU_DATA_DIR")
            or environment.get("BAQYLAU_DATA_DIRECTORY")
            or "~/.local/share/baqylau"
        )
        try:
            port = int(environment.get("BAQYLAU_DASHBOARD_PORT", "8377"))
        except ValueError:
            port = 8377
        return cls(
            data_directory=Path(configured_directory).expanduser().resolve(),
            port=port,
            terminal=environment.get("BAQYLAU_TERMINAL"),
            notify_telegram=environment.get("BAQYLAU_DASHBOARD_NOTIFY_TELEGRAM", "1") != "0",
            notify_webpush=environment.get("BAQYLAU_DASHBOARD_NOTIFY_WEBPUSH", "1") != "0",
            harness_runtime_configs=(
                harness_runtime_configs or default_harness_runtime_configs()
            ),
            base_environment=environment,
        )

    def process_environment(self) -> Mapping[str, str]:
        """Build the environment used by the application and its children."""
        environment = dict(self.base_environment)
        for name in self.environment_removals:
            environment.pop(name, None)
        environment["BAQYLAU_DATA_DIR"] = str(self.data_directory)
        environment["BAQYLAU_DASHBOARD_PORT"] = str(self.port)
        if self.terminal is None:
            environment.pop("BAQYLAU_TERMINAL", None)
        else:
            environment["BAQYLAU_TERMINAL"] = self.terminal
        environment["BAQYLAU_DASHBOARD_NOTIFY_TELEGRAM"] = (
            "1" if self.notify_telegram else "0"
        )
        environment["BAQYLAU_DASHBOARD_NOTIFY_WEBPUSH"] = (
            "1" if self.notify_webpush else "0"
        )
        return environment


@dataclass(frozen=True)
class ApplicationEndpoint:
    host: str
    port: int

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class ApplicationExitReport:
    endpoint: ApplicationEndpoint
    exit_code: int


class DashboardApplication:
    """Build and run the same application for the CLI and for tests."""

    def __init__(self, application_config: ApplicationConfig) -> None:
        self.application_config = application_config

    def run(
        self,
        endpoint_ready: Callable[[ApplicationEndpoint], None] | None = None,
    ) -> ApplicationExitReport:
        process_environment = self.application_config.process_environment()
        os.environ.clear()
        os.environ.update(process_environment)
        configured_endpoint = ApplicationEndpoint(
            host=self.application_config.host,
            port=self.application_config.port,
        )
        try:
            bound_socket = socket.create_server(
                (configured_endpoint.host, configured_endpoint.port)
            )
        except OSError:
            from app import providers  # noqa: PLC0415
            from app.injection import registry, resolve, seed  # noqa: PLC0415

            instances = registry()
            seed(
                instances,
                providers.harness_runtime_configs,
                self.application_config.harness_runtime_configs,
            )
            audit = resolve(instances, providers.recorder)
            audit.error(
                "",
                "dashboard run (port busy)",
                PortAudit(port=configured_endpoint.port),
            )
            return ApplicationExitReport(endpoint=configured_endpoint, exit_code=1)
        endpoint = ApplicationEndpoint(
            host=configured_endpoint.host,
            port=int(bound_socket.getsockname()[1]),
        )
        # Client command lines read this value when the application graph is
        # built. For an automatic bind, the actual port is known only now.
        os.environ["BAQYLAU_DASHBOARD_PORT"] = str(endpoint.port)
        from api import dependencies, server  # noqa: PLC0415
        from app import providers  # noqa: PLC0415
        from app.injection import registry, resolve, seed  # noqa: PLC0415

        instances = registry()
        seed(
            instances,
            providers.harness_runtime_configs,
            self.application_config.harness_runtime_configs,
        )
        policy = resolve(instances, dependencies.policy)
        bound_socket.listen(policy.request_queue_size)
        if endpoint_ready is not None:
            endpoint_ready(endpoint)
        exit_code = server.run_server(bound_socket, instances)
        return ApplicationExitReport(endpoint=endpoint, exit_code=exit_code)
