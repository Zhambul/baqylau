# Copyright (c) 2026 Zhambyl Yermagambet
"""Expose explicit lifecycle outcomes without starting or claiming real jobs."""

from dataclasses import dataclass, replace

from baqylau_extension_api.contracts.lifecycle import ExtensionLifecycle
from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin
from baqylau_extension_api.models import lifecycle

from extensions.registry_package import RegistryPackage
from extensions.registry_snapshot import RuntimeSnapshot
from extensions.runtime_preparation_contract import PreparedExtensionRuntime
from tests.extension_host import registry_fixture


@dataclass
class RetirementProbe(ExtensionPlugin, ExtensionLifecycle, PreparedExtensionRuntime):
    """Count actual retirement calls for one local protocol double."""

    package: RegistryPackage
    stop_reply: lifecycle.DeactivationResult | None = None
    fail_stop: bool = False
    fail_close: bool = False
    close_count: int = 0
    stop_count: int = 0
    resolution: None = None

    @property
    def snapshot(self) -> RuntimeSnapshot:
        """A checked runtime with this test's lifecycle capability."""
        return registry_fixture.snapshot(replace(self.package, plugin=self))

    @property
    def extension_info(self) -> lifecycle.ExtensionInfo:
        """The fixture's exact selected identity."""
        return self.package.entry.extension_info

    @property
    def capabilities(self) -> ExtensionCapabilities:
        """Retain the declared capability set and replace only lifecycle behavior."""
        assert self.package.plugin is not None
        return replace(self.package.plugin.capabilities, lifecycle=self)

    def activate(self, request: lifecycle.ActivationRequest) -> lifecycle.ActivationResult:
        """Accept the exact fixture revision.

        Returns:
            A local readiness result, not private-process evidence.

        """
        return lifecycle.ActivationReady(runtime_revision=request.runtime_revision)

    def deactivate(self, request: lifecycle.DeactivationRequest) -> lifecycle.DeactivationResult:
        """Report configured uncertainty while counting each attempted removal.

        Returns:
            The configured reply or normal acknowledgement.

        Raises:
            RuntimeError: If the test selects a failed lifecycle call.

        """
        self.stop_count += 1
        if self.fail_stop:
            message = "test deactivation failed"
            raise RuntimeError(message)
        return self.stop_reply or lifecycle.DeactivationResult(runtime_revision=request.runtime_revision)

    def close(self) -> None:
        """Count resource closure independently from deactivation acknowledgement.

        Raises:
            RuntimeError: If the test cannot prove complete resource release.

        """
        self.close_count += 1
        if self.fail_close:
            message = "test close failed"
            raise RuntimeError(message)
