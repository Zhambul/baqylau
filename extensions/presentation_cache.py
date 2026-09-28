# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep recent checked terminal views by their complete request.

The request holds the runtime revision, the settings revision, the recorded
snapshot, the pane size, the focus, and the resolved settings. A presenter
shows recorded data, so the same request gives the same view: a repaint with
no new data, settings, or runtime does not call the worker.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from baqylau_extension_api.terminal import models as terminal_models

CACHED_VIEWS = 64


class PresentationCache:
    """Keep the most recent checked views; the oldest leaves first."""

    def __init__(self, limit: int = CACHED_VIEWS) -> None:
        """Start empty with a view limit."""
        self._limit = limit
        self._views: OrderedDict[str, terminal_models.TerminalView] = OrderedDict()
        self._lock = threading.Lock()

    def presented(
        self,
        request: terminal_models.TerminalViewRequest,
        present: Callable[[], terminal_models.TerminalView],
    ) -> terminal_models.TerminalView:
        """Give the kept view of this request, else present and keep it.

        Returns:
            The checked view of the request.

        """
        key = request.model_dump_json()
        with self._lock:
            kept = self._views.get(key)
            if kept is not None:
                self._views.move_to_end(key)
                return kept
        view = present()
        with self._lock:
            self._views[key] = view
            while len(self._views) > self._limit:
                self._views.popitem(last=False)
        return view
