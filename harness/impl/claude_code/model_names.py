# Copyright (c) 2026 Zhambyl Yermagambet
"""Format Claude Code model names for people and limits."""

import re
from collections.abc import Mapping
from types import MappingProxyType

from domain.references import ModelReference
from harness.impl.claude_code.model import (
    FABLE_MODEL,
    HAIKU_MODEL,
    OPUS_MODEL,
    SONNET_MODEL,
)

SHORT_VERSION_PART_MAXIMUM = 2
# A record names a model by an alias or by a `claude-` ID, with an optional `[1m]` window suffix. Each model
# release adds IDs, so a record's model is checked by this shape and not against a list of known models.
RECORD_MODEL = re.compile(r"(?:default|fable|opus|opusplan|sonnet|haiku|claude-[a-z0-9]+(?:[.-][a-z0-9]+)*)(?:\[1m\])?")
ALIAS_DISPLAY: Mapping[str, str] = MappingProxyType({
    FABLE_MODEL: "fable-5",
    OPUS_MODEL: "opus-5",
    SONNET_MODEL: "sonnet-5",
    HAIKU_MODEL: "haiku-4.5",
})


def record_model(model_name: str) -> str:
    """Check the model that a record names.

    Returns:
        The model name, unchanged.

    Raises:
        ValueError: If the name does not have the shape of a Claude Code model.

    """
    if RECORD_MODEL.fullmatch(model_name) is None:
        message = f"{model_name!r} is not a Claude Code model name"
        raise ValueError(message)
    return model_name


def short_model(model: str | None) -> str:
    """Return a short model identifier.

    Returns:
        The short model identifier.

    """
    if not model:
        return ""
    normalized_model = model.lower().replace("[1m]", "").strip()
    normalized_model = normalized_model.removeprefix("claude-")
    parts = normalized_model.split("-")
    version_parts = []
    for version_part in parts[1:]:
        if version_part.isdigit() and len(version_part) <= SHORT_VERSION_PART_MAXIMUM:
            version_parts.append(version_part)
        else:
            break
    version = ".".join(version_parts)
    if version:
        return f"{parts[0]}-{version}"
    return parts[0]


def alias_display(model_name: str) -> str:
    """Return the current display name for a model alias.

    Returns:
        The display name.

    """
    return ALIAS_DISPLAY.get(model_name, model_name)


def display_model(model_reference: ModelReference) -> str:
    """Return the model name that a person sees.

    Returns:
        The display name.

    """
    short_name = short_model(model_reference.name)
    return alias_display(short_name) or model_reference.name


def selection_key(model_name: str) -> str:
    """Compare aliases with their current model, without merging versions.

    Returns:
        The resolved short model name.

    """
    return alias_display(short_model(model_name))
