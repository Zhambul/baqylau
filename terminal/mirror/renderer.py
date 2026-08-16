"""Stateful terminal block renderer with deterministic replacement and reflow."""

from __future__ import annotations

import re
from dataclasses import replace

from terminal.mirror.blocks import (
    TerminalBlank,
    TerminalBlock,
    TerminalLine,
    TerminalRule,
    TerminalStyle,
    TerminalText,
    TerminalUpdate,
)

RESET = "\033[0m"
HEADER = "\033[38;5;244m ◧ command mirror — waiting for commands… \033[0m"


def _style(style: TerminalStyle) -> str:
    codes = []
    if style.foreground is not None:
        color = style.foreground
        codes.append(f"38;2;{color.red};{color.green};{color.blue}")
    if style.background is not None:
        color = style.background
        codes.append(f"48;2;{color.red};{color.green};{color.blue}")
    if style.bold:
        codes.append("1")
    if style.italic:
        codes.append("3")
    if style.underline:
        codes.append("4")
    if style.dim:
        codes.append("2")
    return f'\033[{";".join(codes)}m' if codes else ""


def _text(text: TerminalText) -> str:
    content = text.text
    if text.link_target is not None:
        scheme = "baqylau-view" if text.link_action == "view" else "baqylau-content"
        content = (
            f"\033]8;;{scheme}://{text.link_target}\033\\"
            f"{content}\033]8;;\033\\"
        )
    style = _style(text.style)
    return f"{style}{content}{RESET}" if style else content


def _text_width(parts: tuple[TerminalText, ...] | list[TerminalText]) -> int:
    return sum(len(part.text) for part in parts)


def _render_parts(parts: tuple[TerminalText, ...] | list[TerminalText], width: int,
                  background) -> str:
    if background is None:
        return "".join(_text(part) for part in parts)
    painted = [
        replace(part, style=replace(part.style, background=part.style.background or background))
        for part in parts
    ]
    remaining = max(0, width - _text_width(painted))
    if remaining:
        painted.append(TerminalText(" " * remaining, TerminalStyle(background=background)))
    return "".join(_text(part) for part in painted)


def _take(
    parts: tuple[TerminalText, ...] | list[TerminalText],
    width: int,
) -> tuple[list[TerminalText], list[TerminalText]]:
    taken: list[TerminalText] = []
    remaining = list(parts)
    available = max(0, width)
    while remaining and available:
        part = remaining.pop(0)
        if len(part.text) <= available:
            taken.append(part)
            available -= len(part.text)
            continue
        taken.append(replace(part, text=part.text[:available]))
        remaining.insert(0, replace(part, text=part.text[available:]))
        available = 0
    return taken, remaining


def _render_line(line: TerminalLine, width: int) -> list[str]:
    first_prefix = list(line.prefix)
    continuation_prefix = list(line.continuation_prefix)
    content = list(line.content)
    if line.layout == "verbatim":
        parts = (*first_prefix, *content)
        return [_render_parts(parts, width, line.background)]
    if line.layout == "truncate":
        visible, _remaining = _take((*first_prefix, *content), width)
        return [_render_parts(visible, width, line.background)]
    if line.layout == "word_wrap":
        atoms = []
        for part in content:
            atoms.extend(
                replace(part, text=atom)
                for atom in re.findall(r"[ \t]+|[^ \t]+", part.text)
            )
        rendered = []
        prefix = first_prefix
        current: list[TerminalText] = []
        current_width = 0
        while atoms or not rendered:
            prefix_width = _text_width(prefix)
            available = max(0, width - prefix_width)
            while atoms:
                atom = atoms[0]
                atom_width = len(atom.text)
                if atom.text.isspace() and not current and rendered:
                    atoms.pop(0)
                    continue
                if current and current_width + atom_width > available:
                    break
                atoms.pop(0)
                if atom_width <= available - current_width:
                    current.append(atom)
                    current_width += atom_width
                    continue
                visible, remaining = _take((atom,), available - current_width)
                current.extend(visible)
                atoms[:0] = remaining
                break
            if atoms:
                while current and current[-1].text.isspace():
                    current.pop()
            rendered.append(_render_parts((*prefix, *current), width, line.background))
            if atoms and available == 0:
                raise ValueError("terminal line prefix leaves no room for wrapped content")
            prefix = continuation_prefix
            current = []
            current_width = 0
        return rendered
    rendered = []
    prefix = first_prefix
    while content or not rendered:
        prefix_visible, _unused_prefix = _take(prefix, width)
        available = max(0, width - _text_width(prefix_visible))
        content_visible, content = _take(content, available)
        rendered.append(_render_parts(
            (*prefix_visible, *content_visible), width, line.background
        ))
        if content and available == 0:
            raise ValueError("terminal line prefix leaves no room for wrapped content")
        prefix = continuation_prefix
    return rendered


class TerminalRenderer:
    def __init__(
        self,
        width: int,
        header: str | None = None,
        row_limit: int | None = None,
    ) -> None:
        if width <= 0:
            raise ValueError("terminal width must be positive")
        if row_limit is not None and row_limit <= 0:
            raise ValueError("terminal row limit must be positive")
        self.width = width
        self.header = header
        self.row_limit = row_limit
        self._order: list[str] = []
        self._blocks: dict[str, TerminalBlock] = {}

    def apply(self, update: TerminalUpdate) -> None:
        for block_id in update.remove_block_ids:
            self._blocks.pop(block_id, None)
            if block_id in self._order:
                self._order.remove(block_id)
        for block in update.updated_blocks:
            if block.block_id not in self._blocks:
                self._order.append(block.block_id)
            self._blocks[block.block_id] = block

    def reflow(self, width: int) -> None:
        if width <= 0:
            raise ValueError("terminal width must be positive")
        self.width = width

    def blocks(self) -> tuple[TerminalBlock, ...]:
        return tuple(self._blocks[block_id] for block_id in self._order)

    def ansi(self) -> str:
        rendered_blocks = []
        remaining_rows = (
            None
            if self.row_limit is None
            else max(0, self.row_limit - (1 if self.header is not None else 0))
        )
        for block in reversed(self.blocks()):
            block_rows = []
            for row in block.rows:
                if isinstance(row, TerminalBlank):
                    block_rows.append("")
                elif isinstance(row, TerminalRule):
                    block_rows.append(_text(TerminalText("─" * self.width, row.style)))
                elif isinstance(row, TerminalLine):
                    block_rows.extend(_render_line(row, self.width))
                else:
                    raise TypeError(f"unsupported terminal row: {type(row).__name__}")
            if remaining_rows is not None:
                if remaining_rows == 0:
                    break
                if len(block_rows) > remaining_rows:
                    block_rows = block_rows[-remaining_rows:]
                remaining_rows -= len(block_rows)
            rendered_blocks.append(block_rows)
        rows = [row for block_rows in reversed(rendered_blocks) for row in block_rows]
        prefix = "\033[H\033[2J\033[3J"
        if self.header is not None:
            prefix += self.header + "\n"
        return prefix + "\n".join(rows) + RESET
