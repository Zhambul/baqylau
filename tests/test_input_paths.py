# Copyright (c) 2026 Zhambyl Yermagambet
"""Check exact source paths without native event timing."""

from pathlib import Path

import pytest

from core.input_paths import InputPaths, existing_parent

SOURCE = "source"


@pytest.mark.parametrize(("relative", "directory", "expected"), [
    ("source/events.log", False, True),
    ("source", True, True),
    ("source/other.log", False, False),
    ("source", False, False),
    ("other/events.log", False, False),
])
def test_missing_file_selection(tmp_path: Path, relative: str, *, directory: bool, expected: bool) -> None:
    """Accept the selected file and new parent directories, but not unrelated files."""
    paths = InputPaths(additional=frozenset((tmp_path / SOURCE / "events.log",)))
    assert paths.matches(tmp_path / relative, is_directory=directory) is expected


def test_directory_selection_keeps_children(tmp_path: Path) -> None:
    """A declared directory selects all its descendants, with no suffix filter."""
    paths = InputPaths(additional=frozenset((tmp_path / SOURCE,)))
    assert paths.matches(tmp_path / SOURCE / "nested" / "output", is_directory=False)
    assert not paths.matches(tmp_path / "source-other" / "output", is_directory=False)


def test_removed_paths_keep_core_outputs(tmp_path: Path) -> None:
    """The next complete selection removes only extension path relevance."""
    paths = InputPaths(output_files=frozenset((tmp_path / "core.log",)))
    assert paths.matches(tmp_path / "core.log", is_directory=False)
    assert not paths.matches(tmp_path / "extension.log", is_directory=False)


def test_watch_root_is_bounded_to_directory(tmp_path: Path) -> None:
    """An existing declared directory does not require a recursive parent watch."""
    source = tmp_path / SOURCE
    source.mkdir()
    paths = InputPaths(additional=frozenset((source, tmp_path / "missing" / "input.log")))
    assert paths.roots(()) == {source, tmp_path / "missing"}
    assert existing_parent(tmp_path / "missing") == tmp_path.resolve()


def test_symlink_directory_keeps_parent_watch(tmp_path: Path) -> None:
    """The lexical parent must observe a later symlink replacement."""
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    assert InputPaths(additional=frozenset((link,))).roots(()) == {tmp_path}
