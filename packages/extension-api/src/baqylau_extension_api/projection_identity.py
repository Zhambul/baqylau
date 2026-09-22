# Copyright (c) 2026 Zhambyl Yermagambet
"""Derive stable feed identities without using runtime or rebuild revisions."""

from hashlib import sha256

from baqylau_extension_api.models.base import ExtensionId, OpaqueId, WireModel
from baqylau_extension_api.models.scopes import ExtensionScope


class ProjectionEntryIdentity(WireModel):
    """Name one owner's row for one immutable cause in one scope."""

    extension_id: ExtensionId
    scope: ExtensionScope
    source_event_id: OpaqueId
    entry_key: OpaqueId


def projected_entry_id(identity: ProjectionEntryIdentity) -> str:
    """Use the fixed V1 wire encoding to derive a host storage identity.

    Returns:
        An owned identity independent of settings, worker, and history revisions.

    """
    checked = ProjectionEntryIdentity.model_validate(identity)
    digest = sha256(checked.model_dump_json().encode("utf-8")).hexdigest()
    return f"projected:v1:{checked.extension_id}:{digest}"
