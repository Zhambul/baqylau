# Copyright (c) 2026 Zhambyl Yermagambet
"""Read the processes that live below the program a terminal reports.

A terminal that names only the program it started hides the process that speaks
for a session. OpenCode2 is the case that needs this: its terminal program runs
the native server as a CHILD, and that child is the process that reports every
event. A window that reports only its own program therefore cannot answer the
one question ownership asks — does this window host the process of my session?

The whole table is read at once, because one read answers for every window and
costs the same as a read for one process.
"""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

import psutil

from terminal.models.values import WindowProcess

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

PROCESS_FIELDS = ("pid", "ppid", "cmdline")


class ProcessTree:
    """Answer for the live children of a process from one read of the table."""

    def __init__(self, children: Mapping[int, Sequence[WindowProcess]]) -> None:
        """Keep the children of each process."""
        self._children = {parent_id: tuple(found) for parent_id, found in children.items()}

    @classmethod
    def sample(cls) -> ProcessTree:
        """Read every live process once.

        Returns:
            The tree of the processes that were alive at this moment.

        """
        children: dict[int, list[WindowProcess]] = {}
        for process in psutil.process_iter():
            with suppress(psutil.Error, OSError, SystemError):
                _record(children, process)
        return cls(children)

    def descendants(self, process_id: int | None) -> tuple[WindowProcess, ...]:
        """Return every live process below one process.

        Returns:
            The processes below the one asked for, at any depth.

        """
        if process_id is None:
            return ()
        return self._below(process_id, set())

    def _below(self, process_id: int, seen: set[int]) -> tuple[WindowProcess, ...]:
        seen.add(process_id)
        found: list[WindowProcess] = []
        for child in self._children.get(process_id, ()):
            if child.process_id is None or child.process_id in seen:
                continue
            found.append(child)
            found.extend(self._below(child.process_id, seen))
        return tuple(found)


def _record(children: dict[int, list[WindowProcess]], process: psutil.Process) -> None:
    described = process.as_dict(attrs=PROCESS_FIELDS)
    parent_id = int(described["ppid"] or 0)
    command = tuple(described["cmdline"] or ())
    found = WindowProcess(int(described["pid"]), command)
    children.setdefault(parent_id, []).append(found)
