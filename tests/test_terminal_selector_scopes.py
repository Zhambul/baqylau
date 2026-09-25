# Copyright (c) 2026 Zhambyl Yermagambet
"""The pane selector offers a window's session and the session's repository (P10-T02)."""

from pathlib import Path
from unittest.mock import Mock

from baqylau_extension_api.models.scopes import InstallationScope, RepositoryScope, SessionScope

from api.extensions.terminal_selector_routes import window_scopes
from domain.ids import WindowId
from tests.canonical_sessiondata_api_git import create_linked_worktree

WINDOW = WindowId("window-1")


def window_session(directory: Path) -> tuple[Mock, Mock]:
    """Give a terminal whose window shows one session that works in the directory.

    Returns:
        The terminal and the session store.

    """
    terminal, sessions = Mock(), Mock()
    terminal.session_for_window.return_value = "session-1"
    facts = sessions.read.return_value.session
    facts.session_id = "session-1"
    facts.lead_actor_id = "lead"
    facts.harness = "claude"
    facts.working_directory = str(directory)
    return terminal, sessions


def test_repository_session_offers_it(tmp_path: Path) -> None:
    """The scopes are the installation, the session, and the session's repository."""
    create_linked_worktree(tmp_path / "main", tmp_path / "linked")

    scopes = window_scopes(*window_session(tmp_path / "linked"), WINDOW)

    assert [type(scope) for scope in scopes] == [InstallationScope, SessionScope, RepositoryScope]


def test_plain_session_offers_no_repository(tmp_path: Path) -> None:
    """A session outside a repository offers only the installation and the session."""
    scopes = window_scopes(*window_session(tmp_path), WINDOW)

    assert [type(scope) for scope in scopes] == [InstallationScope, SessionScope]
