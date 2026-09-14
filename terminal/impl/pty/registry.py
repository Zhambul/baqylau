# Copyright (c) 2026 Zhambyl Yermagambet
"""Own the live PTY windows and their launch environment."""

from __future__ import annotations

import os
import threading
from itertools import count
from typing import TYPE_CHECKING
from uuid import uuid4

from terminal.impl.pty.window import PtyWindow, open_window
from terminal.models.values import WindowId

if TYPE_CHECKING:
    from collections.abc import Mapping

    from terminal.models.tabs import EnvironmentVariable

WINDOW_ID_VARIABLE = "BAQYLAU_PTY_WINDOW_ID"
TERM_VARIABLE = "TERM"
TERM_VALUE = "xterm-256color"


class PtyWindows:
    """Own live PTY windows and close their process groups."""

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        """Initialize PTY window operations."""
        self.environment = dict(os.environ if environment is None else environment)
        self.windows: dict[WindowId, PtyWindow] = {}
        self.lock = threading.RLock()
        self._namespace = uuid4().hex
        self._ids = count(1)

    def launch(
        self,
        command: tuple[str, ...],
        working_directory: str,
        environment: tuple[EnvironmentVariable, ...],
    ) -> PtyWindow:
        """Launch one program in a new PTY window.

        Returns:
            The new PTY window.

        """
        with self.lock:
            window_id = WindowId(f"{self._namespace}:{next(self._ids)}")
            child_environment = dict(self.environment)
            launch_environment = {
                environment_variable.name: environment_variable.content for environment_variable in environment
            }
            child_environment.update(launch_environment)
        child_environment[WINDOW_ID_VARIABLE] = window_id
        child_environment[TERM_VARIABLE] = TERM_VALUE
        window = open_window(
            window_id,
            tuple(command),
            working_directory,
            child_environment,
        )
        with self.lock:
            self.windows[window_id] = window
        return window

    def get(self, window_id: WindowId) -> PtyWindow | None:
        """Return one open window.

        Returns:
            The window, or ``None`` when it is not open.

        """
        with self.lock:
            return self.windows.get(window_id)

    def close(self, window_id: WindowId) -> bool:
        """Close one open PTY window.

        Returns:
            True when the window was open and closed.

        """
        with self.lock:
            window = self.windows.pop(window_id, None)
        if window is None:
            return False
        return window.close()

    def close_all(self) -> None:
        """Close all owned process groups."""
        with self.lock:
            window_ids = tuple(self.windows)
        for window_id in window_ids:
            self.close(window_id)
