# Copyright (c) 2026 Zhambyl Yermagambet
"""Prepare checked private workers without changing the active extension set."""

from contextlib import ExitStack, closing
from dataclasses import dataclass, field
from functools import partial

from anyio.from_thread import start_blocking_portal
from baqylau_extension_api.contracts.services import ExtensionHostServices
from baqylau_extension_api.runtime.call_grants import HostCallLedger
from baqylau_extension_api.runtime.preparation import validate_load
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest

from extensions.environment_contract import ExtensionEnvironments
from extensions.impl.process import ownership, runtime
from extensions.models.workers import WorkerPolicy
from extensions.worker_contract import ExtensionWorker, ExtensionWorkers


@dataclass(frozen=True)
class ProcessExtensionWorkers(ExtensionWorkers):
    """Use standard library resource ownership and AnyIO's managed reader thread."""

    environments: ExtensionEnvironments
    ledger: HostCallLedger
    policy: WorkerPolicy = field(default_factory=WorkerPolicy)

    def prepare_worker(self, request: WorkerLoadRequest, services: ExtensionHostServices) -> ExtensionWorker:
        """Check metadata, prepare dependencies, and load the exact fixed package.

        Returns:
            An owned worker that is ready for lifecycle preparation, not yet active.

        Raises:
            ValueError: If the selected artifact has a different manifest.

        """
        validate_load(request, services)
        with ExitStack() as cleanup:
            environment = cleanup.enter_context(closing(self.environments.prepare_environment(
                request.environment.extension_info.package_digest,
            )))
            if environment.artifact.manifest != request.manifest:
                message = "worker manifest does not match the selected captured package"
                raise ValueError(message)
            portal = cleanup.enter_context(start_blocking_portal(name="baqylau-extension-rpc"))
            prepared = cleanup.enter_context(portal.wrap_async_context_manager(runtime.running_worker(
                environment, request, services, self.policy, partial(self.ledger.revoke_runtime, request.environment),
            )))
            return ownership.ManagedExtensionWorker(prepared, cleanup.pop_all())
