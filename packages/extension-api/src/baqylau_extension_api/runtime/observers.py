# Copyright (c) 2026 Zhambyl Yermagambet
"""Run post-commit jobs in the live lane with separate stop and recovery calls."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.observers import ExtensionObserver
from baqylau_extension_api.models.observer_jobs import (
    ObservationCancelRequest,
    ObservationCancelResult,
    ObservationJobRequest,
    ObservationReconcileRequest,
)
from baqylau_extension_api.models.observer_results import ObservationJobResult
from baqylau_extension_api.observers import requests, results
from baqylau_extension_api.runtime import channel, codec, methods, revisions
from baqylau_extension_api.runtime.contract import RemoteCaller
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.schemas import SchemaSet


@dataclass(frozen=True)
class RemoteObserver(ExtensionObserver):
    """Use the public protocol without importing feature code in the host."""

    caller: RemoteCaller

    def observe(self, observation_request: ObservationJobRequest) -> ObservationJobResult:
        """Dispatch once; do not retry an external effect inside the proxy.

        Returns:
            A checked job outcome for later host acceptance.

        """
        response = self.caller.invoke_typed(
            methods.OBSERVE, observation_request, TypeAdapter[ObservationJobResult](ObservationJobResult),
        )
        return results.validate_observation_result(observation_request.binding, response)

    def cancel_observation(self, cancel_request: ObservationCancelRequest) -> ObservationCancelResult:
        """Request a stop without assigning a final stored job state.

        Returns:
            The exact attempt's checked acknowledgment.

        """
        response = self.caller.invoke_typed(
            methods.OBSERVE_CANCEL, cancel_request, TypeAdapter(ObservationCancelResult),
        )
        return results.validate_observation_cancel_result(cancel_request, response)

    def reconcile_observation(self, reconcile_request: ObservationReconcileRequest) -> ObservationJobResult:
        """Inspect proof through recovery, never through observe.

        Returns:
            A proven outcome or explicit uncertainty.

        """
        response = self.caller.invoke_typed(
            methods.OBSERVE_RECONCILE, reconcile_request, TypeAdapter[ObservationJobResult](ObservationJobResult),
        )
        return results.validate_observation_result(reconcile_request.observation.binding, response)


@dataclass(frozen=True)
class WorkerObserver(ExtensionObserver):
    """Validate the complete trigger and output around each feature callback."""

    provider: ExtensionObserver
    load: WorkerLoadRequest
    schemas: SchemaSet

    def observe(self, observation_request: ObservationJobRequest) -> ObservationJobResult:
        """Require a declared live trigger before executing the accepted job.

        Returns:
            Complete checked evidence without storing any result observation.

        """
        request = requests.validate_observation_request(self.load.manifest, self.schemas, observation_request)
        revisions.require_runtime_revision(request.binding.runtime_revision, self.load.environment)
        response = results.validate_observation_result(request.binding, self.provider.observe(request))
        results.validate_observation_documents(self.load.manifest, self.schemas, response)
        return response

    def cancel_observation(self, cancel_request: ObservationCancelRequest) -> ObservationCancelResult:
        """Check owner and runtime before forwarding the stop request.

        Returns:
            A checked stop acknowledgment for the selected attempt.

        """
        request = requests.validate_observation_cancel(self.load.manifest, cancel_request)
        revisions.require_runtime_revision(request.binding.runtime_revision, self.load.environment)
        return results.validate_observation_cancel_result(request, self.provider.cancel_observation(request))

    def reconcile_observation(self, reconcile_request: ObservationReconcileRequest) -> ObservationJobResult:
        """Check declared recovery and its evidence without repeating execution.

        Returns:
            A complete proven or uncertain result.

        """
        request = requests.validate_observation_reconcile(self.load.manifest, self.schemas, reconcile_request)
        revisions.require_runtime_revision(request.observation.binding.runtime_revision, self.load.environment)
        response = results.validate_observation_result(
            request.observation.binding, self.provider.reconcile_observation(request),
        )
        results.validate_observation_documents(self.load.manifest, self.schemas, response)
        return response


def register_observer(
    rpc: channel.RpcChannel, provider: ExtensionObserver, request: WorkerLoadRequest, schemas: SchemaSet,
) -> None:
    """Allow live callbacks and reserve independent control capacity for stops."""
    bound = WorkerObserver(provider, request, schemas)
    rpc.register(methods.OBSERVE, codec.ModelHandler(
        ObservationJobRequest, TypeAdapter(ObservationJobResult), bound.observe,
    ), "live")
    rpc.register(methods.OBSERVE_CANCEL, codec.ModelHandler(
        ObservationCancelRequest, TypeAdapter(ObservationCancelResult), bound.cancel_observation,
    ), "control")
    rpc.register(methods.OBSERVE_RECONCILE, codec.ModelHandler(
        ObservationReconcileRequest, TypeAdapter(ObservationJobResult), bound.reconcile_observation,
    ), "live")
