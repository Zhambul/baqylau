# Copyright (c) 2026 Zhambyl Yermagambet
"""The dashboard's Git queries take no lock in the user's checkout.

The session header reads `git status` with a two-second limit. Under load the
limit killed Git while it held `.git/index.lock`, and the user's next Git
command in that checkout failed until the lock was removed.
"""

from __future__ import annotations

import subprocess  # noqa: S404 -- Run real Git in a private repository.
from typing import TYPE_CHECKING

from core.repository import RepositoryQueries

if TYPE_CHECKING:
    from pathlib import Path


def test_status_leaves_no_index_lock(tmp_path: Path) -> None:
    """A status query of a changed repository neither refreshes nor locks its index."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "file.txt").write_text("one\n", encoding="utf-8")
    _git(tmp_path, "add", "file.txt")
    index = tmp_path / ".git" / "index"
    before = index.stat().st_mtime_ns
    (tmp_path / "file.txt").write_text("two\n", encoding="utf-8")

    status = RepositoryQueries.status(str(tmp_path))

    assert status is not None
    assert status.dirty
    assert index.stat().st_mtime_ns == before
    assert not (tmp_path / ".git" / "index.lock").exists()


def _git(directory: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 -- Fixed arguments in a private repository.
        (RepositoryQueries.git_executable, "-C", str(directory), *arguments), check=True, capture_output=True,
    )
