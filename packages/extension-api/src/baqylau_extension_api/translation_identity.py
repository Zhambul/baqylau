# Copyright (c) 2026 Zhambyl Yermagambet
"""Allocate logical translated fact IDs without using runtime or raw row IDs."""

import hashlib

from baqylau_extension_api.models.base import ExtensionId, OpaqueId, WireModel
from baqylau_extension_api.models.scopes import ExtensionScope


class TranslationIdentity(WireModel):
    """Use an extension-owned key inside the complete selected scope."""

    extension_id: ExtensionId
    scope: ExtensionScope
    fact_key: OpaqueId


def translated_event_id(identity: TranslationIdentity) -> str:
    """Keep repeated observations of the same logical fact on the same ID.

    Returns:
        A versioned ID distinct from raw and canonical transform additions.

    """
    checked = TranslationIdentity.model_validate(identity)
    digest = hashlib.sha256(checked.model_dump_json().encode("utf-8")).hexdigest()
    return f"translated:v1:{checked.extension_id}:{digest}"
