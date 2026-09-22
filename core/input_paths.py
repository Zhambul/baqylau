# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep independent native input subscriptions in one complete selection."""

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path


class InputGroup(Enum):
    """Keep independent file producers from replacing each other's subscriptions."""

    CORE = auto()
    ADDITIONAL = auto()


@dataclass(frozen=True)
class InputPaths:
    """Keep additional inputs separate from core source and output updates."""

    source_directories: frozenset[Path] = frozenset()
    output_files: frozenset[Path] = frozenset()
    core_files: frozenset[Path] = frozenset()
    additional: frozenset[Path] = frozenset()

    def matches(self, path: Path, *, is_directory: bool) -> bool:
        """Select exact inputs, directory children, and new parents of missing inputs.

        Returns:
            True only when the path can affect a selected additional input or output.

        """
        if path in self.output_files:
            return True
        return any(
            path == selected or path.is_relative_to(selected)
            or (is_directory and selected.is_relative_to(path))
            for selected in self.additional
        )

    def roots(self, profiles: tuple[Path, ...]) -> set[Path]:
        """Include parents so replacement and missing-file creation remain observable.

        Returns:
            Requested native watch roots before existing-parent selection.

        """
        return {
            *profiles, *self.source_directories,
            *(path.parent for path in self.output_files),
            *(_additional_root(path) for path in self.additional),
        }


def existing_parent(path: Path) -> Path:
    """Find a native watch root even when the requested input does not exist yet.

    Returns:
        The nearest existing directory with its physical path resolved.

    """
    while not path.is_dir() and path != path.parent:
        path = path.parent
    return path.resolve()


def resolved_inputs(paths: frozenset[Path]) -> frozenset[Path]:
    """Keep lexical links and their physical targets in one watch selection.

    Returns:
        Both path forms, so target writes and link replacement remain observable.

    """
    return paths | frozenset(path.resolve() for path in paths)


def _additional_root(path: Path) -> Path:
    if path.is_dir() and not path.is_symlink():
        return path
    return path.parent
