"""Private terminal drawing model; never persisted or consumed by the dashboard."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

from terminal.models import RGB


@dataclass(frozen=True)
class TerminalStyle:
    foreground: RGB | None = None
    background: RGB | None = None
    bold: bool = False
    italic: bool = False
    underline: bool = False
    dim: bool = False


@dataclass(frozen=True)
class TerminalText:
    text: str
    style: TerminalStyle = TerminalStyle()
    link_target: str | None = None
    link_action: Literal["copy", "view"] = "copy"


@dataclass(frozen=True)
class TerminalLine:
    content: tuple[TerminalText, ...]
    prefix: tuple[TerminalText, ...] = ()
    continuation_prefix: tuple[TerminalText, ...] = ()
    background: RGB | None = None
    layout: Literal["wrap", "word_wrap", "truncate", "verbatim"] = "wrap"


@dataclass(frozen=True)
class TerminalRule:
    style: TerminalStyle = TerminalStyle()


@dataclass(frozen=True)
class TerminalBlank:
    pass


TerminalRow: TypeAlias = TerminalLine | TerminalRule | TerminalBlank


@dataclass(frozen=True)
class TerminalBlock:
    block_id: str
    rows: tuple[TerminalRow, ...]


@dataclass(frozen=True)
class TerminalUpdate:
    updated_blocks: tuple[TerminalBlock, ...] = ()
    remove_block_ids: tuple[str, ...] = ()
