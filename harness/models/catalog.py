"""The menu vocabulary a harness offers — models, efforts, commands, rewind modes."""

from __future__ import annotations

from dataclasses import dataclass

from domain.ids import ModelId, SessionId


@dataclass(frozen=True)
class EffortOption:
    value: str
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

    model_id: ModelId
    display_name: str
    default: bool
    efforts: tuple[EffortOption, ...] = ()


@dataclass(frozen=True)
class CommandOption:
    command: str
    description: str
    minimum_prompt_count: int


@dataclass(frozen=True)
class RewindModeOption:
    value: str
    display_name: str


@dataclass(frozen=True)
class QueryContext:
    session_id: SessionId | None
    working_directory: str | None


@dataclass(frozen=True)
class HarnessCatalogSnapshot:
    """The menu vocabulary that genuinely depends on WHERE the session is.

    Everything a harness offers unconditionally now lives on HarnessInfo, which
    is a frozen literal built once at import. Only the commands remain here,
    because they are discovered by walking the session's own directory -- two
    sessions in different projects have different ones, so no static literal can
    hold them.
    """

    commands: tuple[CommandOption, ...] = ()
