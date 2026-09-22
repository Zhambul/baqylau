# Copyright (c) 2026 Zhambyl Yermagambet
"""Start the actual dashboard application with private data and package roots."""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from api.runtime import ApplicationConfig
from sdk.client import BaqylauClient
from tests.e2e.testkit.process import ApplicationProcess


def application_config(directory: Path, *, read_only: bool = False) -> ApplicationConfig:
    """Build the private application configuration for one test directory.

    Returns:
        A configuration whose data, packages, and terminal are private.

    """
    return ApplicationConfig(
        data_directory=directory, extension_roots=(directory / "packages",), port=0,
        terminal="pty", notify_telegram=False, notify_webpush=False,
        extension_read_only=read_only,
    )


@contextmanager
def running_application(directory: Path) -> Iterator[tuple[BaqylauClient, ApplicationProcess]]:
    """Yield a ready client and its process without requiring a clean stop.

    Yields:
        The ready client and the process which serves it.

    """
    process = ApplicationProcess.start(application_config(directory))
    with ExitStack() as cleanup:
        cleanup.callback(process.stop)
        client = BaqylauClient(process.endpoint.url)
        cleanup.callback(client.close)
        client.application.wait_until_ready()
        yield client, process


@contextmanager
def running_catalog(directory: Path, *, read_only: bool = False) -> Iterator[BaqylauClient]:
    """Use the product process path and the public typed HTTP client.

    Yields:
        A ready client whose process and data are private to this test.

    """
    config = application_config(directory, read_only=read_only)
    process = ApplicationProcess.start(config)
    with ExitStack() as cleanup:
        cleanup.callback(process.stop)
        client = BaqylauClient(process.endpoint.url)
        cleanup.callback(client.close)
        client.application.wait_until_ready()
        yield client
    assert process.stop() == 0
