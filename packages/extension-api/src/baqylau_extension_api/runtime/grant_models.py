# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep immutable host call records separate from transmitted feature data."""

from typing import Annotated

from pydantic import Field, FiniteFloat

from baqylau_extension_api.models.base import ExtensionId, Identifier, WireModel
from baqylau_extension_api.models.environment import ExtensionEnvironment
from baqylau_extension_api.models.scopes import ExtensionScope

MAX_CALL_DEPTH = 16


class HostCallGrant(WireModel):
    """Record one host-selected worker, scope, monotonic deadline, and call route."""

    call_id: Identifier
    environment: ExtensionEnvironment
    scope: ExtensionScope
    expires_at: Annotated[FiniteFloat, Field(gt=0)]
    route: Annotated[tuple[ExtensionId, ...], Field(min_length=1, max_length=MAX_CALL_DEPTH)]
    parents: Annotated[tuple[Identifier, ...], Field(max_length=MAX_CALL_DEPTH - 1)] = ()
