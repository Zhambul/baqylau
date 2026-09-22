# Copyright (c) 2026 Zhambyl Yermagambet
"""Allocate repeatable logical identities for extension additions."""

import hashlib

from baqylau_extension_api.models.base import ExtensionId, Identifier, OpaqueId, WireModel


class DerivedIdentity(WireModel):
    """Name one addition relative to an input, independent of runtime revision."""

    extension_id: ExtensionId
    input_id: OpaqueId
    output_key: Identifier


def derived_event_id(identity: DerivedIdentity) -> str:
    """Build a stable event ID with unambiguous field boundaries.

    The owner and output key cannot contain the separator. The opaque input
    ID can contain it, because both outside fields remain unambiguous.

    Returns:
        The same logical ID for a retry or a different history revision.

    """
    validated = DerivedIdentity.model_validate(identity)
    identity_text = f"{validated.extension_id}\x1f{validated.input_id}\x1f{validated.output_key}"
    digest = hashlib.sha256(identity_text.encode("utf-8")).hexdigest()
    return f"extension:{validated.extension_id}:{digest}"


def derived_input_id(identity: DerivedIdentity) -> str:
    """Keep raw additions separate from canonical additions with the same key.

    Returns:
        A stable derived translation input identity.

    """
    return f"raw:{derived_event_id(identity)}"


def derived_projection_change_id(identity: DerivedIdentity) -> str:
    """Keep projection operations distinct from raw and canonical insertions.

    Returns:
        A stable operation identity without a host commit cursor.

    """
    return f"projection:{derived_event_id(identity)}"
