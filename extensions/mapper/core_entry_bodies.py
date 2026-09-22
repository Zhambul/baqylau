# Copyright (c) 2026 Zhambyl Yermagambet
"""Map the complete feed body vocabulary without open payload dictionaries."""

from baqylau_extension_api.core.entries import CoreEntryBody
from baqylau_extension_api.core.entry_registry import CORE_ENTRY_MODELS
from baqylau_extension_api.errors import ExtensionContractError
from pydantic import TypeAdapter, ValidationError

from domain.entries import BODY_TYPES, ENTRY_TYPES, EntryTypeName
from domain.entry_base import EntryBody

PUBLIC_ENTRY_BODY: TypeAdapter[CoreEntryBody] = TypeAdapter(CoreEntryBody)


def public_body(body: EntryBody) -> CoreEntryBody:
    """Map one known stored body to the closed public union.

    Returns:
        A fully typed body with its public feed tag.

    Raises:
        ExtensionContractError: If the private body type or its data is invalid.

    """
    try:
        return _map_to_public(body)
    except (KeyError, ValidationError) as error:
        message = "core feed body cannot map to the public API"
        raise ExtensionContractError(message) from error


def private_body(body: CoreEntryBody) -> EntryBody:
    """Restore the exact stored body type after public validation.

    Returns:
        A private body without the public discriminator field.

    Raises:
        ExtensionContractError: If the public body is not valid for the host.

    """
    try:
        return _map_to_private(body)
    except (KeyError, ValueError) as error:
        message = "public core feed body cannot map to the host"
        raise ExtensionContractError(message) from error


def _map_to_public(body: EntryBody) -> CoreEntryBody:
    kind = ENTRY_TYPES[type(body)]
    mapped = CORE_ENTRY_MODELS[kind].model_validate(body)
    return PUBLIC_ENTRY_BODY.validate_python(mapped)


def _map_to_private(body: CoreEntryBody) -> EntryBody:
    checked = PUBLIC_ENTRY_BODY.validate_python(body)
    adapter = TypeAdapter(BODY_TYPES[EntryTypeName(checked.kind)])
    return adapter.validate_json(checked.model_dump_json(exclude={"kind"}))
