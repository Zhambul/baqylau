# Copyright (c) 2026 Zhambyl Yermagambet
"""A harness executable that is a link stays a link, so an update does not break a running daemon."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dashboard import cli_options
from domain.ids import HarnessName

if TYPE_CHECKING:
    from pathlib import Path

CLAUDE_CODE = HarnessName("claude_code")


def test_executable_link_is_not_followed(tmp_path: Path) -> None:
    """The daemon keeps the link, which an updater points at each new version."""
    version = tmp_path / "versions" / "1.0.0"
    version.parent.mkdir()
    version.write_bytes(b"")
    link = tmp_path / "claude"
    link.symlink_to(version)

    options = cli_options.launch_options(["--harness-executable", f"{CLAUDE_CODE}={link}"])

    assert options.harness_runtime_configs.for_harness(CLAUDE_CODE).executable == str(link)
