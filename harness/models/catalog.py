# Copyright (c) 2026 Zhambyl Yermagambet
"""The menu vocabulary a harness offers — models, efforts, commands, rewind modes."""

from __future__ import annotations

from dataclasses import dataclass

from domain.ids import SessionId


@dataclass(frozen=True)
class EffortOption:
    """Represent effort option."""

    effort: str
    display_name: str
    default: bool


@dataclass(frozen=True)
class ModelOption:
    """One model a harness offers, with the reasoning levels IT supports.

    The efforts are nested rather than listed once per harness because they are
    model-DEPENDENT: one harness was measured offering a level on some of its
    models and not others, while a single flat list advertised it for all of
    them -- so the picker refused a level the menu had promised. A harness whose
    levels do not vary simply repeats the same tuple on every model.
    """

    model_name: str
    display_name: str
    default: bool
    efforts: tuple[EffortOption, ...] = ()


@dataclass(frozen=True)
class CommandOption:
    """Represent command option."""

    command: str
    description: str
    minimum_prompt_count: int


@dataclass(frozen=True)
class RewindModeOption:
    """Represent rewind mode option."""

    mode: str
    display_name: str


@dataclass(frozen=True)
class QueryContext:
    """Represent query context."""

    session_id: SessionId | None
    working_directory: str | None


@dataclass(frozen=True)
class HarnessCatalogSnapshot:
    """The menu vocabulary that genuinely depends on WHERE the session is.

    Commands can depend on the directory. Models can depend on the native
    account and configuration. None keeps the fixed models from HarnessInfo;
    an empty model tuple means that native discovery found no models.
    """

    commands: tuple[CommandOption, ...] = ()
    models: tuple[ModelOption, ...] | None = None
