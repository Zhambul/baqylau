# Copyright (c) 2026 Zhambyl Yermagambet
"""Call external projectors with typed snapshots through the pure worker lane."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.projection import ExtensionProjector
from baqylau_extension_api.models.projections import (
    ProjectionReadSet,
    ProjectionRequest,
    ProjectionResult,
    ProjectionSelectionRequest,
)
from baqylau_extension_api.projection import inputs, results, selection
from baqylau_extension_api.runtime import channel, codec, methods, revisions
from baqylau_extension_api.runtime.contract import RemoteCaller
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.schemas import SchemaSet


@dataclass(frozen=True)
class RemoteProjector(ExtensionProjector):
    """Call a projector without importing feature code into the host."""

    caller: RemoteCaller

    def select_records(self, selection_request: ProjectionSelectionRequest) -> ProjectionReadSet:
        """Select keys without importing their feature-specific rules into the host.

        Returns:
            A checked list of owned keys at the exact read boundary.

        """
        response = self.caller.invoke_typed(methods.PROJECT_SELECT, selection_request, TypeAdapter(ProjectionReadSet))
        return selection.validate_read_set(selection_request, response)

    def project(self, projection_request: ProjectionRequest) -> ProjectionResult:
        """Require the complete reply to keep its captured record boundary.

        Returns:
            Checked entries and record changes, with no local writes.

        """
        response = self.caller.invoke_typed(methods.PROJECT, projection_request, TypeAdapter(ProjectionResult))
        return results.validate_projection_result(projection_request, response)


@dataclass(frozen=True)
class WorkerProjector(ExtensionProjector):
    """Check declarations and all documents around one pure feature call."""

    provider: ExtensionProjector
    load: WorkerLoadRequest
    schemas: SchemaSet

    def select_records(self, selection_request: ProjectionSelectionRequest) -> ProjectionReadSet:
        """Validate pure key selection before the host performs its snapshot read.

        Returns:
            Only declared collection keys from the selected owner and scope.

        """
        request = inputs.validate_projection_selection(self.load.manifest, self.schemas, selection_request)
        revisions.require_runtime_revision(request.binding.context.runtime_revision, self.load.environment)
        response = selection.validate_read_set(request, self.provider.select_records(request))
        selection.validate_read_registrations(self.load.manifest, self.schemas, response)
        return response

    def project(self, projection_request: ProjectionRequest) -> ProjectionResult:
        """Check input before the callback and its complete output before reply.

        Returns:
            One valid proposal with host writes still pending.

        """
        request = inputs.validate_projection_request(self.load.manifest, self.schemas, projection_request)
        revisions.require_runtime_revision(request.binding.context.runtime_revision, self.load.environment)
        response = results.validate_projection_result(request, self.provider.project(request))
        results.validate_projection_documents(self.load.manifest, self.schemas, response)
        return response


def register_projector(
    rpc: channel.RpcChannel, provider: ExtensionProjector, request: WorkerLoadRequest, schemas: SchemaSet,
) -> None:
    """Run projection in order without live host services or storage writes."""
    bound = WorkerProjector(provider, request, schemas)
    rpc.register(methods.PROJECT_SELECT, codec.ModelHandler(
        ProjectionSelectionRequest, TypeAdapter(ProjectionReadSet), bound.select_records,
    ), "pure")
    rpc.register(methods.PROJECT, codec.ModelHandler(
        ProjectionRequest, TypeAdapter(ProjectionResult), bound.project,
    ), "pure")
