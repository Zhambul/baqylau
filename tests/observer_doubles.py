# Copyright (c) 2026 Zhambyl Yermagambet
"""Answer observer jobs with a fixed observer in an enabled package double."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from baqylau_extension_api.contracts.plugin import ExtensionCapabilities, ExtensionPlugin
from baqylau_extension_api.models import directory, observer_jobs, observer_results

from extensions.registry_package import RegistryPackage
from tests import command_package_fixture as packages
from tests.extension_api import operation_samples, service_samples
from tests.extension_host import lifecycle_fixture

if TYPE_CHECKING:
    from baqylau_extension_api.contracts.lifecycle import ExtensionLifecycle
    from baqylau_extension_api.manifest.package import ExtensionManifest

OWNER = operation_samples.OWNER
MANAGER = lifecycle_fixture.MANAGER


class FakeObserver:
    """Answer one committed trigger with a fresh output observation."""

    def __init__(self) -> None:
        """Start with no observed or reconciled requests."""
        self.requests: list[observer_jobs.ObservationJobRequest] = []
        self.reconciled: list[observer_jobs.ObservationReconcileRequest] = []

    def observe(self, request: observer_jobs.ObservationJobRequest) -> observer_results.ObservationJobResult:
        """Record the request and answer a success that retains the trigger.

        Returns:
            A valid observer success.

        """
        self.requests.append(request)
        return _succeeded(request.binding)

    def cancel_observation(
        self, request: observer_jobs.ObservationCancelRequest,
    ) -> observer_jobs.ObservationCancelResult:
        """Report a proven stop.

        Returns:
            A canceled acknowledgment for the exact attempt.

        """
        return observer_jobs.ObservationCancelResult(binding=request.binding, status="canceled")

    def reconcile_observation(
        self, request: observer_jobs.ObservationReconcileRequest,
    ) -> observer_results.ObservationJobResult:
        """Record the request and resolve it with the same output as observe.

        Returns:
            A valid observer success.

        """
        self.reconciled.append(request)
        return _succeeded(request.observation.binding)


def _succeeded(binding: observer_jobs.ObservationJobBinding) -> observer_results.ObservationSucceeded:
    observation = operation_samples.observation().model_copy(update={"causes": (binding.event_id,)})
    return observer_results.ObservationSucceeded(binding=binding, observations=(observation,))


def an_observer_package(observer: FakeObserver, runtime_revision: str, manifest: ExtensionManifest) -> RegistryPackage:
    """Build an enabled package double with the observer capability.

    Returns:
        The active package selection.

    """
    environment = service_samples.environment(OWNER).model_copy(update={"runtime_revision": runtime_revision})
    capabilities = ExtensionCapabilities(lifecycle=cast("ExtensionLifecycle", None), observer=observer)
    plugin = packages.FakePlugin(extension_info=environment.extension_info, capabilities=capabilities)
    return RegistryPackage(
        manifest=manifest,
        entry=directory.DirectoryEntry(extension_info=environment.extension_info, state="enabled"),
        environment=environment,
        plugin=cast("ExtensionPlugin", plugin),
    )
