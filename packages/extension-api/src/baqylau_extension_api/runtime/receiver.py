# Copyright (c) 2026 Zhambyl Yermagambet
"""Validate revision-bound requests before dispatch to a typed capability."""

from collections.abc import Mapping
from dataclasses import dataclass

from baqylau_extension_api.runtime.codec import encode_parameters
from baqylau_extension_api.runtime.contract import AsyncReceiver, EncodedHandler
from baqylau_extension_api.runtime.execution import WorkerLanes
from baqylau_extension_api.runtime.host_context import host_call_scope
from baqylau_extension_api.runtime.models import ExecutionKind, ExtensionTransportError, RpcEnvelope


@dataclass(frozen=True)
class RpcReceiver(AsyncReceiver):
    """Bind one registered method to a runtime revision and execution lane."""

    runtime_revision: str
    lanes: WorkerLanes
    dispatch: EncodedHandler
    kind: ExecutionKind

    async def receive(self, packet: Mapping[str, str]) -> Mapping[str, str]:
        """Reject stale or invalid requests without invoking extension code.

        Returns:
            A checked result bound to the same runtime revision.

        Raises:
            ExtensionTransportError: If the request is stale or the handler fails.

        """
        envelope = RpcEnvelope.model_validate(packet)
        if envelope.runtime_revision != self.runtime_revision:
            message = "extension request has a stale runtime revision"
            raise ExtensionTransportError(message)
        try:
            with host_call_scope(envelope.host_call_id):
                encoded = await self.lanes.run(self.kind, self.dispatch, envelope.json_text)
        except Exception as exc:
            message = "extension capability call failed"
            raise ExtensionTransportError(message) from exc
        return encode_parameters(RpcEnvelope(
            runtime_revision=self.runtime_revision, json_text=encoded, host_call_id=envelope.host_call_id,
        ))
