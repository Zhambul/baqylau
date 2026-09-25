# Copyright (c) 2026 Zhambyl Yermagambet
"""Build a command package double whose execution waits for the test, in an accepted registry snapshot."""

from __future__ import annotations

from dataclasses import dataclass, replace
from threading import Event
from typing import TYPE_CHECKING, cast

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities

from extensions.registry_package import RegistryPackage
from extensions.registry_snapshot import prepare_snapshot
from tests import command_package_fixture as packages
from tests.extension_api.example import SampleLifecycle
from tests.extension_host.registry_memory_fixture import MemoryRegistry

if TYPE_CHECKING:
    from baqylau_extension_api.models import command_results, commands as sdk_commands


WAIT_SECONDS = 5.0


class SlowCommands(packages.FakeCommands):
    """Block each execution until the test releases it."""

    def __init__(self) -> None:
        """Start blocked, with no started execution."""
        super().__init__()
        self.started = Event()
        self.release = Event()

    def execute(self, request: sdk_commands.CommandRequest) -> command_results.CommandSucceeded:
        """Report the start, then wait for the release.

        Returns:
            The fake success after the release.

        """
        self.started.set()
        assert self.release.wait(WAIT_SECONDS)
        return super().execute(request)


def lifecycle_package(slow_commands: SlowCommands) -> RegistryPackage:
    """Build the command package double with a real lifecycle and no queries or processing.

    Returns:
        The package that a registry snapshot accepts.

    """
    base = packages.a_package(slow_commands)
    contributions = base.manifest.contributions.model_copy(update={"queries": (), "processing": ()})
    manifest = base.manifest.model_copy(update={
        "capabilities": ("lifecycle", "commands"), "contributions": contributions,
    })
    capabilities = ExtensionCapabilities(lifecycle=SampleLifecycle(), commands=slow_commands)
    plugin = replace(cast("packages.FakePlugin", base.plugin), capabilities=capabilities)
    return replace(base, manifest=manifest, plugin=plugin)


def a_registry(package: RegistryPackage) -> MemoryRegistry:
    """Publish one snapshot with the package.

    Returns:
        The registry with the accepted snapshot.

    """
    registry = MemoryRegistry("initial")
    assert package.environment is not None
    snapshot = prepare_snapshot(1, package.environment.runtime_revision, (package,))
    assert registry.publish_snapshot(0, snapshot).status == "accepted"
    return registry


@dataclass(frozen=True)
class SlowCase:
    """Keep the blocking commands, their package, and the registry that publishes the package."""

    slow_commands: SlowCommands
    package: RegistryPackage
    registry: MemoryRegistry


def a_slow_case() -> SlowCase:
    """Build blocking commands in a package that one accepted snapshot publishes.

    Returns:
        The case.

    """
    slow_commands = SlowCommands()
    package = lifecycle_package(slow_commands)
    return SlowCase(slow_commands, package, a_registry(package))
