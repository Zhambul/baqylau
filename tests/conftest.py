"""Shared safety settings for the canonical test suite."""

from __future__ import annotations

import os
import sys

import pytest

REPOSITORY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPOSITORY_ROOT not in sys.path:
    sys.path.insert(0, REPOSITORY_ROOT)


@pytest.fixture(autouse=True)
def isolated_application_files(monkeypatch, tmp_path):
    """Keep current-schema application state out of the user's data directory.

    One environment variable moves all three databases now, because
    `core/data.py` is the one owner of their paths. The uploads directory is
    still patched by attribute: it is resolved at import, and it is the only
    place the application writes bytes rather than rows.
    """
    from dashboard import paths

    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BAQYLAU_RUNTIME_DIRECTORY", str(tmp_path / "runtime"))
    monkeypatch.setattr(paths, "UPLOADS_DIRECTORY", str(tmp_path / "uploads"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("BAQYLAU_TERMINAL", "none")
    # Hooks record their tab's terminal window as evidence; the suite itself may
    # run inside a terminal, and that identity must not leak into fixtures.
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
