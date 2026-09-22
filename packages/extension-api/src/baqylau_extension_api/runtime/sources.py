# Copyright (c) 2026 Zhambyl Yermagambet
"""Run live source capabilities through the public typed process boundary."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.sources import ExtensionSources
from baqylau_extension_api.models.source_results import SourcePlan, SourceReadResult, SourceReleaseResult
from baqylau_extension_api.models.sources import SourceContext, SourceReadRequest, SourceReleaseRequest
from baqylau_extension_api.runtime import channel, codec, methods, revisions
from baqylau_extension_api.runtime.contract import RemoteCaller
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.sources import batches, plans, registration


@dataclass(frozen=True)
class RemoteSources(ExtensionSources):
    """Call a source provider without importing its implementation."""

    caller: RemoteCaller

    def describe(self, source_context: SourceContext) -> SourcePlan:
        """Read a complete source plan through the process adapter.

        Returns:
            A checked plan tied to the selected scope and call.

        """
        response = self.caller.invoke_typed(methods.SOURCES_DESCRIBE, source_context, TypeAdapter(SourcePlan))
        return plans.validate_source_plan(source_context, response)

    def read(self, source_request: SourceReadRequest) -> SourceReadResult:
        """Read original observations without committing host progress.

        Returns:
            A checked source batch or an explicit read failure.

        """
        response = self.caller.invoke_typed(
            methods.SOURCES_READ, source_request, TypeAdapter[SourceReadResult](SourceReadResult),
        )
        return batches.validate_source_batch(source_request, response)

    def release(self, release_request: SourceReleaseRequest) -> SourceReleaseResult:
        """Release the selected source or scope.

        Returns:
            Its exact complete or pending release acknowledgment.

        """
        response = self.caller.invoke_typed(methods.SOURCES_RELEASE, release_request, TypeAdapter(SourceReleaseResult))
        return plans.validate_source_release(release_request, response)


@dataclass(frozen=True)
class WorkerSources(ExtensionSources):
    """Check declarations and complete proposals around each live source call."""

    provider: ExtensionSources
    load: WorkerLoadRequest
    schemas: SchemaSet

    def describe(self, source_context: SourceContext) -> SourcePlan:
        """Validate the selected scope before creating source resources.

        Returns:
            A completely validated source plan.

        """
        request = registration.validate_source_context(self.load.manifest, self.schemas, source_context)
        revisions.require_runtime_revision(request.binding.runtime_revision, self.load.environment)
        response = plans.validate_source_plan(request, self.provider.describe(request))
        plans.validate_plan_documents(self.load.manifest, self.schemas, response)
        return response

    def read(self, source_request: SourceReadRequest) -> SourceReadResult:
        """Validate source selection before reading and output before replying.

        Returns:
            Checked observations and progress for later atomic host storage.

        """
        request = registration.validate_source_read(self.load.manifest, self.schemas, source_request)
        revisions.require_runtime_revision(request.context.binding.runtime_revision, self.load.environment)
        response = batches.validate_source_batch(request, self.provider.read(request))
        batches.validate_batch_documents(self.load.manifest, self.schemas, response)
        return response

    def release(self, release_request: SourceReleaseRequest) -> SourceReleaseResult:
        """Reject stale ownership before releasing any source.

        Returns:
            A checked release acknowledgment.

        """
        request = SourceReleaseRequest.model_validate(release_request)
        registration.validate_source_binding(self.load.manifest, request.binding)
        revisions.require_runtime_revision(request.binding.runtime_revision, self.load.environment)
        return plans.validate_source_release(request, self.provider.release(request))


def register_sources(
    rpc: channel.RpcChannel, provider: ExtensionSources, request: WorkerLoadRequest, schemas: SchemaSet,
) -> None:
    """Run source reads outside the ordered pure processing lane."""
    bound = WorkerSources(provider, request, schemas)
    rpc.register(methods.SOURCES_DESCRIBE, codec.ModelHandler(
        SourceContext, TypeAdapter(SourcePlan), bound.describe,
    ), "live")
    rpc.register(methods.SOURCES_READ, codec.ModelHandler(
        SourceReadRequest, TypeAdapter(SourceReadResult), bound.read,
    ), "live")
    rpc.register(methods.SOURCES_RELEASE, codec.ModelHandler(
        SourceReleaseRequest, TypeAdapter(SourceReleaseResult), bound.release,
    ), "live")
