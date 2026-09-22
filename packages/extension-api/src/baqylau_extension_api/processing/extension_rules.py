# Copyright (c) 2026 Zhambyl Yermagambet
"""Protect extension document ownership during canonical transforms."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.models.canonical import CanonicalFact
from baqylau_extension_api.models.events import ExtensionFact


def validate_replacement(original: ExtensionFact, replacement: ExtensionFact, owner: str) -> None:
    """Allow peer data changes only within its original type and schema.

    Raises:
        ExtensionContractError: If a replacement changes ownership or source causes.

    """
    if replacement.document.schema_ref.owner != original.document.schema_ref.owner:
        message = "replacement cannot change a schema's owner"
        raise ExtensionContractError(message)
    if replacement.causes != original.causes:
        message = "replacement cannot change the input causes"
        raise ExtensionContractError(message)
    if original.document.schema_ref.owner != owner:
        same_type = replacement.event_type == original.event_type
        same_schema = replacement.document.schema_ref == original.document.schema_ref
        if not same_type or not same_schema:
            message = "a peer fact must keep its event type and exact schema"
            raise ExtensionContractError(message)


def validate_insertion(original: CanonicalFact, addition: ExtensionFact, owner: str) -> None:
    """Require an extension-owned event type and a link to the input.

    Raises:
        ExtensionContractError: If ownership or the required cause is invalid.

    """
    if addition.document.schema_ref.owner != owner:
        message = "an extension can add only its own event types"
        raise ExtensionContractError(message)
    if original.event_id not in addition.causes:
        message = "an inserted extension fact must name its input cause"
        raise ExtensionContractError(message)
