# Copyright (c) 2026 Zhambyl Yermagambet
"""Serve the actual daemon with private backend-free packages for browser controls."""

import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from api.runtime import ApplicationConfig, ApplicationEndpoint, DashboardApplication
from tests.extension_host import lifecycle_dependency_fixture, package_fixture


def report_endpoint(application_endpoint: ApplicationEndpoint) -> None:
    """Report only the private test listener to the parent browser runner."""
    sys.stdout.write(f"BAQYLAU_FIXTURE_URL={application_endpoint.url}\n")
    sys.stdout.flush()


def main() -> int:
    """Run real management and storage without a normal user data directory.

    Returns:
        The actual daemon exit code after normal shutdown.

    """
    with TemporaryDirectory(prefix="baqylau-extension-browser-") as temporary:
        directory = Path(temporary)
        lifecycle_dependency_fixture.write_graph(directory)
        package_fixture.write_package(directory / "packages", "test.failed")
        package_fixture.write_file(directory / "packages", "broken/extension.json", b"{")
        application = DashboardApplication(ApplicationConfig(
            data_directory=directory, extension_roots=(directory / "packages",),
            port=0, terminal="pty", notify_telegram=False, notify_webpush=False,
            extension_read_only=os.environ.get("BAQYLAU_E2E_EXTENSION_READ_ONLY") == "1",
        ))
        return application.run(report_endpoint).exit_code


if __name__ == "__main__":
    sys.exit(main())
