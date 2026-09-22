# Copyright (c) 2026 Zhambyl Yermagambet
"""Call typed external projection transforms without host feature imports."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.projection import ExtensionProjectionTransformer
from baqylau_extension_api.models.projection_transforms import ProjectionTransformRequest, ProjectionTransformResult
from baqylau_extension_api.projection_transform import inputs, results
from baqylau_extension_api.runtime import channel, codec, methods, revisions
from baqylau_extension_api.runtime.contract import RemoteCaller
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.schemas import SchemaSet


@dataclass(frozen=True)
class RemoteProjectionTransformer(ExtensionProjectionTransformer):
    """Return explicit operations through the same protocol as local feature code."""

    caller: RemoteCaller

    def transform(self, projection_request: ProjectionTransformRequest) -> ProjectionTransformResult:
        """Reject a reply for another captured input or projection snapshot.

        Returns:
            A complete typed proposal that the host must validate before commit.

        """
        response = self.caller.invoke_typed(
            methods.PROJECTION_TRANSFORM, projection_request, TypeAdapter(ProjectionTransformResult),
        )
        return results.validate_transform_reply(projection_request, response)


@dataclass(frozen=True)
class WorkerProjectionTransformer(ExtensionProjectionTransformer):
    """Validate complete input and all output operations around the pure callback."""

    provider: ExtensionProjectionTransformer
    load: WorkerLoadRequest
    schemas: SchemaSet

    def transform(self, projection_request: ProjectionTransformRequest) -> ProjectionTransformResult:
        """Check registration, protected core state, schemas, and final write targets.

        Returns:
            Validated operations without any host data write.

        """
        request = inputs.validate_transform_request(self.load.manifest, self.schemas, projection_request)
        revisions.require_runtime_revision(request.binding.context.runtime_revision, self.load.environment)
        response = results.validate_transform_reply(request, self.provider.transform(request))
        results.apply_projection_transform(self.load.manifest, request, response, self.schemas)
        return response


def register_projection_transformer(
    rpc: channel.RpcChannel, provider: ExtensionProjectionTransformer, request: WorkerLoadRequest, schemas: SchemaSet,
) -> None:
    """Run derived-data transformations in the ordered pure lane."""
    bound = WorkerProjectionTransformer(provider, request, schemas)
    rpc.register(methods.PROJECTION_TRANSFORM, codec.ModelHandler(
        ProjectionTransformRequest, TypeAdapter(ProjectionTransformResult), bound.transform,
    ), "pure")
