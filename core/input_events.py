# Copyright (c) 2026 Zhambyl Yermagambet
"""Receive native input changes without scanning on a timer."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from core.input_paths import InputGroup, InputPaths, existing_parent
from core.kernel_events import KernelEvents

if TYPE_CHECKING:
    from watchdog.observers.api import ObservedWatch


class InputEvents(FileSystemEventHandler):
    """Watch input directories, open files, and harness processes."""

    def __init__(self, changed: Callable[[], None], profiles: tuple[Path, ...]) -> None:
        """Set up directory discovery and direct file-write notices."""
        self._changed = changed
        self._profiles = profiles
        self._observer = Observer()
        self._watches: dict[Path, ObservedWatch] = {}
        self._paths = InputPaths()
        self._lock = Lock()
        self._kernel = KernelEvents(changed)

    def start(self) -> None:
        """Start the native observer before the initial input read."""
        self._observer.start()
        self._kernel.start()

    def watch_files(self, paths: set[Path], *, input_group: InputGroup = InputGroup.CORE) -> None:
        """Register direct writes before the next read, including replacement files."""
        with self._lock:
            if input_group is InputGroup.CORE:
                self._paths = replace(self._paths, core_files=frozenset(paths))
            else:
                self._paths = replace(self._paths, additional=frozenset(paths))
        if input_group is InputGroup.ADDITIONAL:
            self.update(set(self._paths.source_directories), set(self._paths.output_files))
        if self._kernel.update_files(set(self._paths.core_files | self._paths.additional)):
            self._changed()

    def watch_processes(self, process_ids: set[int]) -> None:
        """Receive harness process exit notices through the same native wait."""
        self._kernel.update(process_ids)

    def update(self, source_directories: set[Path], output_files: set[Path]) -> None:
        """Watch current inputs, including the parents of missing files."""
        with self._lock:
            self._paths = replace(
                self._paths, source_directories=frozenset(source_directories), output_files=frozenset(output_files),
            )
        roots = self._paths.roots(self._profiles)
        roots = {existing_parent(root) for root in roots}
        roots = {
            root for root in roots
            if not any(parent in roots for parent in root.parents)
        }
        for root in self._watches.keys() - roots:
            self._observer.unschedule(self._watches.pop(root))
        for root in roots - self._watches.keys():
            self._watches[root] = self._observer.schedule(
                self, str(root), recursive=True,
            )

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Signal only input writes, moves, and deletions."""
        if event.event_type not in {"created", "modified", "moved", "deleted"}:
            return
        paths = (
            Path(str(event.src_path)),
            Path(str(event.dest_path)),
        )
        with self._lock:
            relevant = any(
                self._paths.matches(path, is_directory=event.is_directory) or _is_harness_input(path)
                for path in paths
            )
        if relevant:
            self._changed()

    def close(self) -> None:
        """Stop and join the observer."""
        self._observer.stop()
        self._observer.join()
        self._kernel.close()


def _is_harness_input(path: Path) -> bool:
    return (
        path.suffix in {".jsonl", ".sqlite", ".sqlite-wal"}
        or (
            path.suffix == ".json"
            and any(part in {"tasks", "teams"} for part in path.parts)
        )
    )
