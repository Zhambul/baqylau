# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep linked build policy changes inside the frontend source stamp."""

from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest

from dashboard import frontend_build_inputs

type SourceReader = Callable[[Path], tuple[bytes, bytes]]


def changed_input(
    original: SourceReader, selected: Path, path: Path,
) -> tuple[bytes, bytes]:
    """Change one input's bytes without writing to the host source tree.

    Returns:
        The original name and selected test content.

    """
    relative, content = original(path)
    if path == selected:
        return relative, b"\n".join((content, b"changed policy input"))
    return relative, content


@pytest.mark.parametrize("filename", frontend_build_inputs.POLICY_CONFIGURATION_FILES)
def test_shared_policy_changes_source_digest(monkeypatch: pytest.MonkeyPatch, filename: str) -> None:
    """Reject stale builds when shared compiler or build configuration changes."""
    original = frontend_build_inputs.source_input
    before = frontend_build_inputs.source_digest()

    selected = frontend_build_inputs.POLICY_DIRECTORY / filename
    monkeypatch.setattr(frontend_build_inputs, "source_input", partial(changed_input, original, selected))
    assert frontend_build_inputs.source_digest() != before


def test_npm_link_configuration_is_a_build_input() -> None:
    """Include the file that selects editable local package resolution."""
    assert frontend_build_inputs.FRONTEND_DIRECTORY / ".npmrc" in frontend_build_inputs.source_files()
