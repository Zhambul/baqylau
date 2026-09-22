# Copyright (c) 2026 Zhambyl Yermagambet
"""Protect canonical identity, scope, ownership, and source metadata."""

from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.identities import DerivedIdentity, derived_event_id
from baqylau_extension_api.models.canonical import CanonicalFact, CoreFact
from baqylau_extension_api.models.events import ExtensionFact
from baqylau_extension_api.models.transforms import Insert
from baqylau_extension_api.processing import extension_rules
from baqylau_extension_api.schemas import SchemaSet


def validate_replacement(original: CanonicalFact, replacement: CanonicalFact, owner: str) -> None:
    """Permit data changes while preserving an input's identity and origin.

    Raises:
        ExtensionContractError: If protected fields or a peer's schema change.

    """
    if original.event_id != replacement.event_id or original.scope != replacement.scope:
        message = "replacement cannot change input identity or scope"
        raise ExtensionContractError(message)
    if isinstance(original, CoreFact) and isinstance(replacement, CoreFact):
        _require_core_origin(replacement, original)
    elif isinstance(original, ExtensionFact) and isinstance(replacement, ExtensionFact):
        extension_rules.validate_replacement(original, replacement, owner)
    else:
        message = "replacement cannot change the canonical envelope kind"
        raise ExtensionContractError(message)


def validate_insertion(original: CanonicalFact, operation: Insert[CanonicalFact], owner: str) -> None:
    """Require host-derived identity, matching scope, and valid local ownership.

    Raises:
        ExtensionContractError: If identity, origin, scope, or ownership is invalid.

    """
    expected_id = derived_event_id(DerivedIdentity(
        extension_id=owner, input_id=original.event_id, output_key=operation.output_key,
    ))
    if operation.document.event_id != expected_id or operation.document.scope != original.scope:
        message = "inserted fact must use its derived identity and input scope"
        raise ExtensionContractError(message)
    if isinstance(operation.document, ExtensionFact):
        extension_rules.validate_insertion(original, operation.document, owner)
    elif isinstance(original, CoreFact):
        _require_core_origin(operation.document, original)
    else:
        message = "a core addition requires a core input anchor"
        raise ExtensionContractError(message)


def validate_document(fact: CanonicalFact, schemas: SchemaSet) -> None:
    """Validate an extension document with the registered immutable schemas."""
    if isinstance(fact, ExtensionFact):
        schemas.validate(fact.document)


def _require_core_origin(actual: CoreFact, expected: CoreFact) -> None:
    actual_origin = (
        actual.turn_id, actual.parent_actor_id, actual.terminal_window_id,
        actual.harness_process_id, actual.raw_event_ids,
    )
    expected_origin = (
        expected.turn_id, expected.parent_actor_id, expected.terminal_window_id,
        expected.harness_process_id, expected.raw_event_ids,
    )
    if actual_origin != expected_origin:
        message = "transform cannot change core source metadata"
        raise ExtensionContractError(message)
