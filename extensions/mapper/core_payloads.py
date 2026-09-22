# Copyright (c) 2026 Zhambyl Yermagambet
"""Map every registered core payload without open document dictionaries."""

from baqylau_extension_api.core.payloads import CorePayload
from baqylau_extension_api.core.registry import CORE_MODELS
from baqylau_extension_api.errors import ExtensionContractError
from pydantic import TypeAdapter, ValidationError

from domain import events
from domain.event_base import EventPayload

PUBLIC_PAYLOAD: TypeAdapter[CorePayload] = TypeAdapter(CorePayload)


def public_payload(payload: EventPayload) -> CorePayload:
    """Validate and map a private payload to its public tagged model.

    Returns:
        A member of the closed public core union.

    Raises:
        ExtensionContractError: If the private type is unknown or invalid.

    """
    try:
        return _map_to_public(payload)
    except (KeyError, ValidationError) as error:
        message = "core payload cannot map to the public API"
        raise ExtensionContractError(message) from error


def private_payload(payload: CorePayload) -> EventPayload:
    """Remove the public tag and restore the authoritative private type.

    Returns:
        The corresponding private payload.

    Raises:
        ExtensionContractError: If the public payload is unknown or invalid.

    """
    try:
        return _map_to_private(payload)
    except (KeyError, ValidationError) as error:
        message = "public core payload cannot map to the host"
        raise ExtensionContractError(message) from error


def _map_to_public(payload: EventPayload) -> CorePayload:
    event_type = events.EVENT_TYPES[type(payload)]
    selected = CORE_MODELS[event_type].model_validate(payload)
    return PUBLIC_PAYLOAD.validate_python(selected)


def _map_to_private(payload: CorePayload) -> EventPayload:
    validated = PUBLIC_PAYLOAD.validate_python(payload)
    adapter = TypeAdapter(events.PAYLOAD_TYPES[validated.kind])
    return adapter.validate_json(validated.model_dump_json(exclude={"kind"}))
