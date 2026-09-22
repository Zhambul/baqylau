# Copyright (c) 2026 Zhambyl Yermagambet
"""Call pure extension decoders with complete captured input and state."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.sources import ExtensionTranslator
from baqylau_extension_api.models.translation_inputs import ExtensionTranslationRequest
from baqylau_extension_api.models.translation_results import ExtensionTranslationResult
from baqylau_extension_api.runtime import channel, codec, methods, revisions
from baqylau_extension_api.runtime.contract import RemoteCaller
from baqylau_extension_api.runtime.worker_models import WorkerLoadRequest
from baqylau_extension_api.schemas import SchemaSet
from baqylau_extension_api.translation import inputs, outputs, results


@dataclass(frozen=True)
class RemoteTranslator(ExtensionTranslator):
    """Call a decoder through its public protocol without feature imports."""

    caller: RemoteCaller

    def translate(self, translation_request: ExtensionTranslationRequest) -> ExtensionTranslationResult:
        """Translate captured bytes and require the exact processing boundary.

        Returns:
            Complete decisions and proposed next state.

        """
        response = self.caller.invoke_typed(
            methods.TRANSLATE, translation_request, TypeAdapter(ExtensionTranslationResult),
        )
        return results.validate_translation_result(translation_request, response)


@dataclass(frozen=True)
class WorkerTranslator(ExtensionTranslator):
    """Validate complete decoder input and output around a pure feature call."""

    provider: ExtensionTranslator
    load: WorkerLoadRequest
    schemas: SchemaSet

    def translate(self, translation_request: ExtensionTranslationRequest) -> ExtensionTranslationResult:
        """Reject unowned input, stale state replies, and invalid candidate schemas.

        Returns:
            A checked proposal; the worker does not update host decoder state.

        """
        request = inputs.validate_translation_request(self.load.manifest, self.schemas, translation_request)
        revisions.require_runtime_revision(request.context.runtime_revision, self.load.environment)
        response = results.validate_translation_result(request, self.provider.translate(request))
        outputs.validate_translation_documents(self.load.manifest, self.schemas, request, response)
        return response


def register_translator(
    rpc: channel.RpcChannel, provider: ExtensionTranslator, request: WorkerLoadRequest, schemas: SchemaSet,
) -> None:
    """Run translation in order without access to live host services."""
    bound = WorkerTranslator(provider, request, schemas)
    rpc.register(methods.TRANSLATE, codec.ModelHandler(
        ExtensionTranslationRequest, TypeAdapter(ExtensionTranslationResult), bound.translate,
    ), "pure")
