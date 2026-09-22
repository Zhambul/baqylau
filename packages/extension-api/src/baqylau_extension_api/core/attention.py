# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe typed questions and answers in public core facts."""

from baqylau_extension_api.core.base import CoreModel
from baqylau_extension_api.models.base import OpaqueId


class AttentionChoice(CoreModel):
    """Describe one selectable answer."""

    label: str
    description: str | None = None


class AttentionPrompt(CoreModel):
    """Describe one question and its choices."""

    prompt_id: OpaqueId
    title: str | None
    prompt: str
    multiple: bool
    choices: tuple[AttentionChoice, ...]


class AttentionAnswer(CoreModel):
    """Keep the selected labels for one question."""

    prompt_id: OpaqueId
    labels: tuple[str, ...]
