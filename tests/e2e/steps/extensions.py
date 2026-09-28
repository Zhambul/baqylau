# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that install extension packages into the live application, and disable them again.

A scenario's harness really runs shell commands and really answers prompts, so
each package gets real session facts. The adapters calls are the real CLI's,
chosen so that they only read: a short logs query, and a `--help` call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pytest_bdd import given, parsers, when

from tests.e2e.testkit.extension_context import ExtensionContext, sdk_wheel
from tests.e2e.testkit.extension_packages import ExtensionPackages

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sdk.client import BaqylauClient
    from tests.e2e.testkit.policy import WaitPolicy
    from tests.e2e.testkit.process import ApplicationProcess
    from tests.e2e.testkit.references import Sessions


@pytest.fixture(scope="session")
def extension_sdk_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the SDK wheel once for each worker.

    Returns:
        The wheel.

    """
    return sdk_wheel(tmp_path_factory.mktemp("extension-sdk"))


@pytest.fixture
def extension_context(
    application_process: ApplicationProcess,
    extension_sdk_wheel: Path,
    client: BaqylauClient,
    sessions: Sessions,
    wait_policy: WaitPolicy,
) -> Iterator[ExtensionContext]:
    """Give the scenario's packages and reads, and disable the packages when it ends.

    Yields:
        The context.

    """
    packages = ExtensionPackages(application_process, extension_sdk_wheel)
    yield ExtensionContext(packages, client, sessions, wait_policy)
    packages.disable_all()


@given(parsers.parse('extension package "{name}" is enabled'))
def package_is_enabled(extension_context: ExtensionContext, name: str) -> None:
    """Install and enable one package."""
    extension_context.packages.enable(name)


@when(parsers.parse('I disable extension package "{name}"'))
def disable_package(extension_context: ExtensionContext, name: str) -> None:
    """Disable one enabled package."""
    extension_context.packages.disable(name)
