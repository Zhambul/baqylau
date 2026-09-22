# Copyright (c) 2026 Zhambyl Yermagambet
"""Protect source ownership while allowing derived raw content changes."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.identities import DerivedIdentity, derived_input_id
from baqylau_extension_api.models.events import RawInput
from baqylau_extension_api.models.transforms import Insert


def validate_source(original: RawInput, changed: RawInput) -> None:
    """Preserve source, scope, and translator selection.

    Raises:
        ExtensionContractError: If a proposal changes protected source fields.

    """
    if _source_context(changed) != _source_context(original):
        message = "raw transforms must preserve scope, source, origin, owner, and source type"
        raise ExtensionContractError(message)


def _source_context(source: RawInput) -> tuple[object, ...]:
    return source.scope, source.source, source.origin, source.owner, source.source_type


def validate_replacement(original: RawInput, changed: RawInput) -> None:
    """Allow new content but retain the replacement input identity.

    Raises:
        ExtensionContractError: If the input identity changes.

    """
    validate_source(original, changed)
    if changed.input_id != original.input_id:
        message = "raw replacements must retain the input identity"
        raise ExtensionContractError(message)


def validate_insertion(original: RawInput, operation: Insert[RawInput], owner: str) -> None:
    """Require a stable addition with the same recorded source context.

    Raises:
        ExtensionContractError: If the addition identity is not derived from its anchor.

    """
    validate_source(original, operation.document)
    expected = derived_input_id(DerivedIdentity(
        extension_id=owner, input_id=original.input_id, output_key=operation.output_key,
    ))
    if operation.document.input_id != expected:
        message = "raw insertion identity must match its owner, input, and output key"
        raise ExtensionContractError(message)
