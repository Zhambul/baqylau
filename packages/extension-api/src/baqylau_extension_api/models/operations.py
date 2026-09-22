# Copyright (c) 2026 Zhambyl Yermagambet
"""Bind operations and query cursors to exact inputs and runtime revisions."""

from baqylau_extension_api.models.base import Digest, ExtensionId, Identifier, NonemptyText, Revision, WireModel
from baqylau_extension_api.models.scopes import ExtensionScope, SnapshotCursor


class OperationBinding(WireModel):
    """Identify one call without exposing a worker or transport request object."""

    extension_id: ExtensionId
    operation_id: Identifier
    scope: ExtensionScope
    runtime_revision: Identifier
    call_id: Identifier


class QuerySnapshot(WireModel):
    """Name the state used by a read, with an optional stored-data cursor."""

    state_revision: Identifier
    cursor: SnapshotCursor | None = None


class QuerySelection(WireModel):
    """Keep page tokens tied to the same operation, scope, settings, and arguments."""

    extension_id: ExtensionId
    operation_id: Identifier
    scope: ExtensionScope
    runtime_revision: Identifier
    settings_revision: Revision
    arguments_digest: Digest


class QueryPageCursor(WireModel):
    """Carry an opaque package token within a host-checked selection and snapshot."""

    selection: QuerySelection
    snapshot: QuerySnapshot
    position: NonemptyText
