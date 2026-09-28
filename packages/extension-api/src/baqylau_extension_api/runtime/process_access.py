# Copyright (c) 2026 Zhambyl Yermagambet
"""Call the host process and inference routes from a worker, and register them on the host channel."""

from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.contracts.processes import ExtensionInferenceService, ExtensionProcessService
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.inference import InferenceRequest, InferenceResult
from baqylau_extension_api.models.processes import ProcessRequest, ProcessResult
from baqylau_extension_api.runtime import channel, codec, methods
from baqylau_extension_api.runtime.contract import RemoteCaller

PROCESS_RESULT: TypeAdapter[ProcessResult] = TypeAdapter(ProcessResult)
INFERENCE_RESULT: TypeAdapter[InferenceResult] = TypeAdapter(InferenceResult)


@dataclass(frozen=True)
class RemoteProcessService(ExtensionProcessService):
    """Run a declared program only in the live lane; pure calls cannot reach the host."""

    caller: RemoteCaller

    def run_process(self, process_request: ProcessRequest) -> ProcessResult:
        """Run one declared program through the host.

        Returns:
            The exit code and output, or the failed bound.

        Raises:
            ExtensionContractError: If the reply names another program.

        """
        response = self.caller.invoke_typed(methods.PROCESS_RUN, process_request, PROCESS_RESULT)
        if response.name != process_request.name:
            message = "process reply changed its requested program"
            raise ExtensionContractError(message)
        return response


@dataclass(frozen=True)
class RemoteInferenceService(ExtensionInferenceService):
    """Send a prompt only in the live lane."""

    caller: RemoteCaller

    def infer(self, inference_request: InferenceRequest) -> InferenceResult:
        """Send one prompt through the host.

        Returns:
            The model's text, or the unavailable state.

        """
        return self.caller.invoke_typed(methods.INFERENCE, inference_request, INFERENCE_RESULT)


def register_process_access(rpc: channel.RpcChannel, processes: ExtensionProcessService) -> None:
    """Register the host process callback bound to one worker connection."""
    process_handler = codec.ModelHandler(ProcessRequest, PROCESS_RESULT, processes.run_process)
    rpc.register(methods.PROCESS_RUN, process_handler, "live")


def register_inference_access(rpc: channel.RpcChannel, inference: ExtensionInferenceService) -> None:
    """Register the host inference callback bound to one worker connection."""
    inference_handler = codec.ModelHandler(InferenceRequest, INFERENCE_RESULT, inference.infer)
    rpc.register(methods.INFERENCE, inference_handler, "live")
