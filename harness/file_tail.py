"""Read complete appended lines without reopening an unchanged file."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class FileMarker:
    """The file values that change when an append-only feed changes."""

    inode: int
    modified_at: int
    size: int


@dataclass(frozen=True)
class CompleteLine:
    """One complete line and the byte position where it starts."""

    position: int
    content: bytes


class CompleteLineTail:
    """A stat-gated reader for one append-only line feed.

    The durable database position remains the authority. The local marker is
    only an idle fast path. A new object, a changed position, or a changed file
    always reads from the durable position again.
    """

    def __init__(self, path: str) -> None:
        self.path = os.path.realpath(path)
        self._idle_position: str | None = None
        self._idle_marker: FileMarker | None = None
        self._idle_known = False

    def read(
        self,
        after_position: str | None,
        limit: int,
    ) -> tuple[CompleteLine, ...]:
        if limit <= 0:
            raise ValueError("line limit must be positive")
        marker = self._path_marker()
        if (
            self._idle_known
            and after_position == self._idle_position
            and marker == self._idle_marker
        ):
            return ()
        try:
            source = open(self.path, "rb")
        except FileNotFoundError:
            self._remember_idle(after_position, None)
            return ()
        lines: list[CompleteLine] = []
        with source:
            if after_position is not None:
                source.seek(int(after_position))
                skipped = source.readline()
                if not skipped.endswith(b"\n"):
                    self._remember_idle(after_position, self._file_marker(source.fileno()))
                    return ()
            for _ in range(limit):
                line_position = source.tell()
                line = source.readline()
                if not line or not line.endswith(b"\n"):
                    break
                lines.append(CompleteLine(line_position, line))
            marker = self._file_marker(source.fileno())
        if lines:
            self._idle_known = False
        else:
            self._remember_idle(after_position, marker)
        return tuple(lines)

    def _path_marker(self) -> FileMarker | None:
        try:
            status = os.stat(self.path)
        except FileNotFoundError:
            return None
        return self._marker(status)

    @classmethod
    def _file_marker(cls, descriptor: int) -> FileMarker:
        return cls._marker(os.fstat(descriptor))

    @staticmethod
    def _marker(status: os.stat_result) -> FileMarker:
        return FileMarker(status.st_ino, status.st_mtime_ns, status.st_size)

    def _remember_idle(
        self,
        after_position: str | None,
        file_marker: FileMarker | None,
    ) -> None:
        self._idle_position = after_position
        self._idle_marker = file_marker
        self._idle_known = True
