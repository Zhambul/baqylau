# Copyright (c) 2026 Zhambyl Yermagambet
"""Dispatch API mapping for canonical entry bodies."""

from __future__ import annotations

from typing import TYPE_CHECKING

from api.sessiondata import (
    attention_body_mapper,
    conversation_body_mapper,
    extension_body_mapper,
    lifecycle_body_mapper,
    resource_body_mapper,
    shell_body_mapper,
)

if TYPE_CHECKING:
    from api.sessiondata.models import entry as entry_models
    from domain import entry_base


def entry_body(entry_body_value: entry_base.EntryBody) -> entry_models.EntryBodyResponse:
    """Return the API response for a canonical entry body.

    Returns:
        The API response for a canonical entry body.

    Raises:
        TypeError: If no mapper supports the entry body type.

    """
    for mapped in (
        conversation_body_mapper.map_body(entry_body_value),
        shell_body_mapper.map_body(entry_body_value),
        resource_body_mapper.map_body(entry_body_value),
        attention_body_mapper.map_body(entry_body_value),
        lifecycle_body_mapper.map_body(entry_body_value),
        extension_body_mapper.map_body(entry_body_value),
    ):
        if mapped is not None:
            return mapped
    msg = f"unmapped entry body: {type(entry_body_value).__name__}"
    raise TypeError(msg)
