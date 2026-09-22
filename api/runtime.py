# Copyright (c) 2026 Zhambyl Yermagambet
"""The explicit configuration and runtime of one dashboard application."""

import os
import socket
from collections.abc import Callable
from dataclasses import dataclass

from api.runtime_config import ApplicationConfig as ApplicationConfig
from app import injection
from audit.documents import PortAudit


@dataclass(frozen=True)
class ApplicationEndpoint:
    """Represent application endpoint."""

    host: str
    port: int

    @property
    def url(self) -> str:
        """Dashboard URL."""
        return f"http://{self.host}:{self.port}"


@dataclass(frozen=True)
class ApplicationExitReport:
    """Represent application exit report."""

    endpoint: ApplicationEndpoint
    exit_code: int


class DashboardApplication:
    """Build and run the same application for the CLI and for tests."""

    def __init__(self, application_config: ApplicationConfig) -> None:
        """Initialize the object."""
        self.application_config = application_config

    def run(
        self,
        endpoint_ready: Callable[[ApplicationEndpoint], None] | None = None,
    ) -> ApplicationExitReport:
        """Run run.

        Returns:
            The application exit report.

        """
        process_environment = self.application_config.process_environment()
        os.environ.clear()
        os.environ.update(process_environment)
        configured_endpoint = ApplicationEndpoint(
            host=self.application_config.host,
            port=self.application_config.port,
        )
        try:
            bound_socket = socket.create_server(
                (configured_endpoint.host, configured_endpoint.port),
            )
        except OSError:
            return self._port_busy_report(configured_endpoint)
        return self._serve(bound_socket, configured_endpoint, endpoint_ready)

    def _port_busy_report(
        self,
        application_endpoint: ApplicationEndpoint,
    ) -> ApplicationExitReport:
        from app import provider_audit_storage  # noqa: PLC0415 -- Apply the process environment first.

        instances = self._instances()
        audit = injection.resolve(instances, provider_audit_storage.recorder)
        audit.error(
            "",
            "dashboard run (port busy)",
            PortAudit(port=application_endpoint.port),
        )
        return ApplicationExitReport(endpoint=application_endpoint, exit_code=1)

    def _serve(
        self,
        bound_socket: socket.socket,
        application_endpoint: ApplicationEndpoint,
        endpoint_ready: Callable[[ApplicationEndpoint], None] | None,
    ) -> ApplicationExitReport:
        endpoint = ApplicationEndpoint(
            host=application_endpoint.host,
            port=int(bound_socket.getsockname()[1]),
        )
        # Client command lines read this value when the application graph is
        # built. For an automatic bind, the actual port is known only now.
        os.environ["BAQYLAU_DASHBOARD_PORT"] = str(endpoint.port)
        from api import dependencies, server  # noqa: PLC0415

        instances = self._instances()
        policy = injection.resolve(instances, dependencies.policy)
        bound_socket.listen(policy.request_queue_size)
        if endpoint_ready is not None:
            endpoint_ready(endpoint)
        exit_code = server.run_server(bound_socket, instances)
        return ApplicationExitReport(endpoint=endpoint, exit_code=exit_code)

    def _instances(self) -> injection.Instances:
        from app import provider_runtime  # noqa: PLC0415 -- Apply the process environment first.

        instances = injection.registry()
        injection.seed(
            instances,
            provider_runtime.harness_runtime_configs,
            self.application_config.harness_runtime_configs,
        )
        return instances
