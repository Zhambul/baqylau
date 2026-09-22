# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep runtime capture and source processing behind narrow engine contracts."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class ExtensionSourceWatches(Protocol):
    """Apply native input watches before any selected file is read."""

    def watch_sources(self, paths: frozenset[Path]) -> None:
        """Replace only extension source subscriptions, including parents of missing paths."""
        ...


class ExtensionSourceBatch(Protocol):
    """Use borrowed capabilities only inside the selected runtime batch."""

    def read_sources(
        self, watches: ExtensionSourceWatches, stopped: Callable[[], bool], *, refresh_plans: bool,
    ) -> float | None:
        """Read bounded source work and return its next absolute deadline, if any."""
        ...
