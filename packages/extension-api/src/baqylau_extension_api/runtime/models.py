# Copyright (c) 2026 Zhambyl Yermagambet
"""Bound each process call and bind it to one prepared runtime revision."""

from typing import Annotated, Literal

from pydantic import Field, FiniteFloat

from baqylau_extension_api.models.base import Identifier, WireModel
from baqylau_extension_api.models.content import MAX_BATCH_BYTES

MAX_RPC_BYTES = 4 * MAX_BATCH_BYTES
MAX_HEADER_BYTES = 8192
type ExecutionKind = Literal["pure", "live", "control"]
RequestTimeout = Annotated[FiniteFloat, Field(gt=0)]


class RpcEnvelope(WireModel):
    """Carry a typed model's JSON inside the library-owned JSON-RPC envelope."""

    runtime_revision: Identifier
    json_text: Annotated[str, Field(min_length=1, max_length=MAX_RPC_BYTES)]
    host_call_id: Identifier | None = None


class ExtensionTransportError(RuntimeError):
    """Report a process boundary failure without exposing a remote traceback."""
