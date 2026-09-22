# Copyright (c) 2026 Zhambyl Yermagambet
"""Adapt the JSON-RPC library's parameter mapping to strict typed models."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import TypeAdapter

from baqylau_extension_api.models.base import WireModel
from baqylau_extension_api.runtime.contract import EncodedHandler
from baqylau_extension_api.runtime.models import RpcEnvelope


def encode_parameters(envelope: RpcEnvelope) -> Mapping[str, str]:
    """Expose only declared envelope fields to the RPC library serializer.

    Returns:
        The library parameter object, never an engine or feature document.

    """
    packet: dict[str, str] = {"runtime_revision": envelope.runtime_revision, "json_text": envelope.json_text}
    if envelope.host_call_id is not None:
        packet["host_call_id"] = envelope.host_call_id
    return packet


@dataclass(frozen=True)
class ModelHandler[Request: WireModel, Response](EncodedHandler):
    """Validate both sides of a capability call with its public model types."""

    request_model: type[Request]
    response_adapter: TypeAdapter[Response]
    callback: Callable[[Request], Response]

    def invoke(self, encoded: str) -> str:
        """Call a typed method after strict process-boundary validation.

        Returns:
            The validated response encoded as JSON.

        """
        request = self.request_model.model_validate_json(encoded)
        response = self.response_adapter.validate_python(self.callback(request), strict=True)
        return self.response_adapter.dump_json(response).decode("utf-8")
