# Copyright (c) 2026 Zhambyl Yermagambet
"""Stored prompts, choices, and answers for user attention."""

from dataclasses import dataclass

from domain.ids import QuestionId
from domain.stored import STORED


@dataclass(frozen=True)
class AttentionChoice:
    """Hold one selectable answer label and its description."""

    __pydantic_config__ = STORED

    label: str
    description: str | None = None


@dataclass(frozen=True)
class AttentionPrompt:
    """Hold one question and the choices that a person can select.

    `free_text` tells if a person can type an answer that is not a choice.
    The default is true, because a stored prompt from before this field
    always showed a text input.
    """

    __pydantic_config__ = STORED

    prompt_id: QuestionId
    title: str | None
    prompt: str
    multiple: bool
    choices: tuple[AttentionChoice, ...]
    free_text: bool = True


@dataclass(frozen=True)
class AttentionAnswer:
    """Hold the labels that a person selected for one prompt."""

    __pydantic_config__ = STORED

    prompt_id: QuestionId
    labels: tuple[str, ...]
