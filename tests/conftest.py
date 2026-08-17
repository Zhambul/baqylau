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

    One environment variable moves both databases AND the uploads directory,
    because `core/data.py` is the one owner of where our files live and nothing
    resolves a path at import any more.
    """
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    monkeypatch.setenv("BAQYLAU_TERMINAL", "none")
    # Hooks record their tab's terminal window as evidence; the suite itself may
    # run inside a terminal, and that identity must not leak into fixtures.
    monkeypatch.delenv("KITTY_WINDOW_ID", raising=False)
