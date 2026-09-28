# Copyright (c) 2026 Zhambyl Yermagambet
"""Define terminal mirror styles and values."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import NamedTuple

from _render_width import text_width

RGB_COMPONENT_COUNT = 3


class Color(NamedTuple):
    red: int
    green: int
    blue: int

    @classmethod
    def from_hex(cls, hexadecimal: str) -> Color:
        """Create a color from a six-digit hexadecimal value.

        Returns:
            The decoded color.

        Raises:
            ValueError: If the value does not contain three color bytes.

        """
        components = bytes.fromhex(hexadecimal.removeprefix("#"))
        if len(components) != RGB_COMPONENT_COUNT:
            message = "a color hexadecimal value must contain six digits"
            raise ValueError(message)
        return cls(components[0], components[1], components[2])


RESET = "\033[0m"


CLEAR = "\033[H\033[2J\033[3J"


HEADER_TEXT = " ◧ command mirror — waiting for commands… "


HEADER_COLOR = Color.from_hex("#808080")


SCROLLBACK_ROWS = 4800


SECONDS_PER_HOUR = 60 * 60


THOUSAND_COUNT = 1_000


MILLION_COUNT = THOUSAND_COUNT * THOUSAND_COUNT


TEXT = Color.from_hex("#aab9d2")


MUTED = Color.from_hex("#858fa6")


DIM = Color.from_hex("#5c6370")


USER = Color.from_hex("#61afef")


SUCCESS = Color.from_hex("#98c379")


FAILURE = Color.from_hex("#e06c75")


WORKING = Color.from_hex("#d19a66")


MODIFIED = Color.from_hex("#e5c07b")


DARK = Color.from_hex("#181a1e")


PRIMARY_TEXT = Color.from_hex("#abb2bf")


SEPARATOR = " · "


TEXT_INDENT = "  "


VERBATIM_LAYOUT = "verbatim"


TRUNCATE_LAYOUT = "truncate"


REMOVED_DIFF_ROW = "removed"


REMOVED_BACKGROUND = Color.from_hex("#371f24")


ADDED_BACKGROUND = Color.from_hex("#1d3226")


REMOVED_CHANGED_BACKGROUND = Color.from_hex("#672a32")


ADDED_CHANGED_BACKGROUND = Color.from_hex("#2b573a")


COPY_SCHEME = "baqylau-content://%s/%s/%s"


VIEW_SCHEME = "baqylau-view://%s/%s/%s"


Links = Callable[[str], str]


EMPTY_ENTRY_IDS: frozenset[str] = frozenset()


_STATISTIC_FIELDS = (
    "prompt_count",
    "shell_command_count",
    "failed_shell_command_count",
    "file_count",
    "lines_added",
    "lines_removed",
    "actor_message_count",
)


TASK_MARKERS = MappingProxyType({
    "completed": ("✓", SUCCESS),
    "in_progress": ("▸", WORKING),
    "pending": ("·", MUTED),
})


TASK_ROWS = 6


FILE_VERBS = MappingProxyType({
    "read": ("Read", USER),
    "created": ("Write", SUCCESS),
    "updated": ("Update", MODIFIED),
    "deleted": ("Delete", FAILURE),
    "renamed": ("Move", MODIFIED),
})


PLAN_DECISIONS = MappingProxyType({
    "approved": "APPROVED",
    "changes_requested": "CHANGES REQUESTED",
    "rejected": "REJECTED",
})


QUIET_FOR_THE_LEAD = frozenset(("message", "reasoning"))


class _ScorePart(NamedTuple):
    text: str
    color: Color


class _SpanStyle(NamedTuple):
    color: Color | None
    background: Color | None
    bold: bool
    dim: bool
    link: str | None


class _SpanSplit(NamedTuple):
    taken: list[Span]
    remaining: list[Span]


@dataclass(eq=False, slots=True)
class Span:
    """Represent span.

    A run of text and how it is painted. The whole style vocabulary the two
        surfaces use — there is no italic or underline here because nothing the pane
        draws asks for one.
    """

    text: str
    color: Color | None = None
    background: Color | None = None
    bold: bool = False
    dim: bool = False
    link: str | None = None

    def style(self) -> _SpanStyle:
        return _SpanStyle(self.color, self.background, self.bold, self.dim, self.link)

    def sized(self, text: str) -> Span:
        return Span(
            text,
            self.color,
            self.background,
            bold=self.bold,
            dim=self.dim,
            link=self.link,
        )

    def ansi(self) -> str:
        text = self.text
        if self.link is not None:
            text = f"\033]8;;{self.link}\033\\{text}\033]8;;\033\\"
        codes = []
        if self.color is not None:
            codes.append(_color_code("38", self.color))
        if self.background is not None:
            codes.append(_color_code("48", self.background))
        if self.bold:
            codes.append("1")
        if self.dim:
            codes.append("2")
        if not codes:
            return text
        joined_codes = ";".join(codes)
        return f"\x1b[{joined_codes}m{text}{RESET}"


def _color_code(prefix: str, color: Color) -> str:
    channels = f"{color.red};{color.green};{color.blue}"
    return f"{prefix};2;{channels}"


def spans_width(spans: Iterable[Span]) -> int:
    return sum(text_width(span.text) for span in spans)
