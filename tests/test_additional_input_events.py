# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify native watches for extension inputs with no harness file suffix."""

import sys
from pathlib import Path

import pytest

from core.input_paths import InputGroup
from tests import additional_input_fixture as fixture

# Linux has no open-file notices, and inotify follows a replaced directory's inode.
MACOS_ONLY = pytest.mark.skipif(sys.platform != "darwin", reason="needs kqueue file notices and path-based FSEvents")


def test_core_updates_keep_extension_watch(tmp_path: Path) -> None:
    """Core subscription changes do not remove an extension's open-file notices."""
    path = tmp_path / "extension.log"
    path.touch()
    with fixture.watching(path) as source:
        source.inputs.update(set(), {tmp_path / "core.log"})
        source.inputs.watch_files({tmp_path / "core.log"})
        source.append()
        source.inputs.update(set(), set())
        source.inputs.watch_files(set())
        source.append()


def test_missing_source_parent_creation(tmp_path: Path) -> None:
    """Creating a missing input tree sends a notice, then its new file remains watched."""
    path = tmp_path / "missing" / "nested" / "events.log"
    with fixture.watching(path) as source:
        path.parent.mkdir(parents=True)
        path.write_text("first\n", encoding="utf-8")
        assert source.changed.wait(5)
        source.select()
        source.append()


@MACOS_ONLY
def test_directory_replacement_keeps_child_writes(tmp_path: Path) -> None:
    """A replaced source directory is registered again before its next input read."""
    path = tmp_path / "source"
    path.mkdir()
    with fixture.watching(path) as source:
        (path / "child.log").touch()
        assert source.changed.wait(5)
        source.changed.clear()
        path.rename(tmp_path / "old-source")
        path.mkdir()
        assert source.changed.wait(5)
        source.select()
        (path / "new-child.log").touch()
        assert source.changed.wait(5)


def test_symlink_target_and_replacement(tmp_path: Path) -> None:
    """Read notices cover physical target writes and replacement of the lexical link."""
    target = tmp_path / "first.log"
    target.touch()
    link = tmp_path / "selected.log"
    link.symlink_to(target)
    with fixture.watching(link) as source:
        source.append()
        replacement = tmp_path / "second.log"
        replacement.touch()
        source.replace_link(replacement)
        source.append()


@MACOS_ONLY
def test_removing_extension_watch_keeps_core_file(tmp_path: Path) -> None:
    """Clearing additional inputs cannot remove a core direct file watch."""
    path = tmp_path / "core.log"
    path.touch()
    with fixture.watching(tmp_path / "extension.log") as source:
        source.inputs.watch_files({path})
        source.inputs.watch_files(set(), input_group=InputGroup.ADDITIONAL)
        source.path = path
        source.append()


def test_missing_top_source_keeps_watches(tmp_path: Path) -> None:
    """A source directory missing at the top of the file system does not watch the whole disk."""
    path = tmp_path / "extension.log"
    path.touch()
    missing = Path(path.anchor) / "baqylau-missing-source"
    assert not missing.exists()
    with fixture.watching(path) as source:
        source.inputs.update({missing}, set())
        source.append()
