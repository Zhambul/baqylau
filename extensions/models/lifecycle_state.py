# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe stored lifecycle state without claiming that any worker is alive."""

from typing import Annotated, Literal

from baqylau_extension_api.models.base import Identifier, Revision, WireModel
from pydantic import Field

from extensions.models.cleanup import ShutdownRecord
from extensions.models.lifecycle_operations import LifecycleOperation, Timestamp
from extensions.models.lifecycle_selection import ExtensionIntent, OwnerSettings, RuntimeSelection


class LifecycleState(WireModel):
    """Read requested state, accepted settings, and the last committed runtime."""

    revision: Revision = 0
    manager_id: Identifier | None = None
    committed_runtime: RuntimeSelection | None = None
    pending_operation: Identifier | None = None
    intents: Annotated[tuple[ExtensionIntent, ...], Field(max_length=1000)] = ()
    settings: Annotated[tuple[OwnerSettings, ...], Field(max_length=1000)] = ()
    last_shutdown: ShutdownRecord | None = None


class LifecycleWrite(WireModel):
    """Return a complete current snapshot after success or a stale write."""

    accepted: bool
    state: LifecycleState


class LifecycleAdmission(WireModel):
    """Separate idempotent replay, stale state, and another pending operation."""

    status: Literal["accepted", "replayed", "stale", "busy"]
    state: LifecycleState
    operation: LifecycleOperation | None = None


class ManagerClaim(WireModel):
    """Select the expected head before a new exclusive daemon run takes ownership."""

    expected_revision: Revision
    manager_id: Identifier
    claimed_at: Timestamp
