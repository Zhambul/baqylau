"""The activity pane's width: what you remembered, and what is configured.

The repository answers "is there a stored width for this project"; the DEFAULT
is policy and lives here, resolved once instead of at both of the store's exits.
"""

from __future__ import annotations

import os

from repository.contract.terminal import PaneWidthRepository

DEFAULT_WIDTH_PERCENT = 25
DEFAULT_RESIZE_COLUMNS = 4


def _configured_integer(name: str, default: int) -> int:
    configured = os.environ.get(name)
    return default if not configured else int(configured)


def configured_width_percent() -> int:
    width = _configured_integer("BAQYLAU_ACTIVITY_WIDTH_PERCENT", DEFAULT_WIDTH_PERCENT)
    if not 1 <= width <= 99:
        raise ValueError("activity pane width must be between 1 and 99 percent")
    return width


def resize_columns() -> int:
    columns = _configured_integer("BAQYLAU_ACTIVITY_RESIZE_COLUMNS", DEFAULT_RESIZE_COLUMNS)
    if columns <= 0:
        raise ValueError("activity pane resize step must be positive")
    return columns


class PaneWidthService:
    def __init__(self, pane_width_repository: PaneWidthRepository) -> None:
        self.widths = pane_width_repository

    def width_percent(self, working_directory: str) -> int:
        """The remembered width for this project, else the configured default."""
        stored = self.widths.width_percent(os.path.realpath(working_directory))
        return stored if stored is not None else configured_width_percent()

    def remember_width(self, working_directory: str, width_percent: int) -> None:
        if not 1 <= width_percent <= 99:
            raise ValueError("activity pane width must be between 1 and 99 percent")
        self.widths.remember_width(os.path.realpath(working_directory), width_percent)

    @staticmethod
    def configured_width_percent() -> int:
        return configured_width_percent()

    @staticmethod
    def resize_columns() -> int:
        return resize_columns()
