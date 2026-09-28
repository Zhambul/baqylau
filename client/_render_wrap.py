# Copyright (c) 2026 Zhambyl Yermagambet
"""Split terminal mirror rendering."""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from _render_span_operations import _painted, _take
from _render_styles import (
    CLEAR,
    RESET,
    SCROLLBACK_ROWS,
    Color,
    Span,
    spans_width,
)
from _render_width import text_width

if TYPE_CHECKING:
    from collections.abc import Iterable

_ATOMS = re.compile(r"[ \t]+|[^ \t]+")


@dataclass
class _WrapState:
    atoms: deque[Span]
    line_prefix: list[Span]
    continuation: list[Span]
    width: int
    background: Color | None
    painted: list[str] = field(default_factory=list)
    current: list[Span] = field(default_factory=list)

    @classmethod
    def from_content(
        cls,
        content: list[Span],
        first: list[Span],
        rest: list[Span],
        width: int,
        background: Color | None,
    ) -> _WrapState:
        atoms = deque(_content_atoms(content))
        return cls(atoms, first, rest, width, background)

    def paint(self) -> list[str]:
        while self.atoms or not self.painted:
            self._fill_line()
            while self.atoms and self.current and self.current[-1].text.isspace():
                self.current.pop()
            self.painted.append(
                _painted(self.line_prefix + self.current, self.width, self.background),
            )
            self.line_prefix = self.continuation
            self.current = []
        return self.painted

    def _fill_line(self) -> None:
        available = max(1, self.width - spans_width(self.line_prefix))
        while self.atoms:
            atom = self.atoms[0]
            if self._discard_leading_space(atom):
                self.atoms.popleft()  # no leading space on a wrap
                continue
            if self._append_atom(atom, available):
                break

    def _discard_leading_space(self, atom: Span) -> bool:
        return atom.text.isspace() and not self.current and bool(self.painted)

    def _append_atom(self, atom: Span, available: int) -> bool:
        if _does_atom_wrap(self.current, atom, available):
            return True
        self.atoms.popleft()
        room = available - spans_width(self.current)
        if text_width(atom.text) <= room:
            self.current.append(atom)
            return False
        head, tail = _take([atom], room)  # a word longer than the line
        if not head and not self.current:
            head, tail = _first_character(atom)
        self.current.extend(head)
        self.atoms.extendleft(reversed(tail))
        return True


def _first_character(atom: Span) -> tuple[list[Span], list[Span]]:
    # A wide character wider than the whole line still takes one row.
    first = atom.sized(atom.text[:1])
    return [first], [atom.sized(atom.text[1:])]


def _does_atom_wrap(current: list[Span], atom: Span, available: int) -> bool:
    if not current:
        return False
    current_width = spans_width(current)
    return current_width + text_width(atom.text) > available


def _content_atoms(content: list[Span]) -> list[Span]:
    atoms: list[Span] = []
    for span in content:
        atoms.extend(
            span.sized(atom)
            for atom in _ATOMS.findall(span.text)
        )
    return atoms


def _split_lines(spans: list[Span]) -> list[list[Span]]:
    logical: list[list[Span]] = [[]]
    for span in spans:
        parts = span.text.split("\n")
        for index, part in enumerate(parts):
            if index:
                logical.append([])
            if part:
                logical[-1].append(span.sized(part))
    return logical


def screen(painted: list[str], header: Iterable[str] = ()) -> str:
    """Return the screen.

    A whole pane: clear, an optional header, and the rows that fit.

    Returns:
        Screen.

    """
    visible_rows = [*header, *painted[-SCROLLBACK_ROWS:]]
    return CLEAR + "\n".join(visible_rows) + RESET
