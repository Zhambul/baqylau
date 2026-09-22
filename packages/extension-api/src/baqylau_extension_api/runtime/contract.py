# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep encoded calls inside the runtime adapter, not in engine contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import TypeAdapter

    from baqylau_extension_api.models.base import WireModel


class EncodedHandler(Protocol):
    """Adapt one typed capability method to its encoded process payload."""

    def invoke(self, encoded: str) -> str:
        """Validate the request, call the capability, and validate its result."""


class RemoteCaller(Protocol):
    """Adapt synchronous public capabilities to a background RPC reader."""

    def invoke_typed[Response](
        self, rpc_method: str, rpc_request: WireModel, rpc_response_adapter: TypeAdapter[Response],
    ) -> Response:
        """Return one validated model while the transport reader stays active."""


class AsyncReceiver(Protocol):
    """Dispatch validated library parameters without stopping the reader."""

    async def receive(self, packet: Mapping[str, str]) -> Mapping[str, str]:
        """Return one encoded model using the same declared envelope fields."""
