# Copyright (c) 2026 Zhambyl Yermagambet
"""Steps that release active browser work."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pytest_bdd import parsers, when

BROWSER_ACTIVE_RELEASE = ".baqylau-browser-active-release"

if TYPE_CHECKING:
    from sdk.client import BaqylauClient
    from tests.e2e.testkit.references import Sessions


@when(parsers.parse('I release active browser work in session "{session_name}"'))
def release_active_browser_work(client: BaqylauClient, sessions: Sessions, session_name: str) -> None:
    """Release active browser work with the default marker."""
    working_directory = client.sessions.snapshot(sessions.get(session_name)).session_data.session.working_directory
    Path(working_directory, BROWSER_ACTIVE_RELEASE).write_text("release\n", encoding="utf-8")


@when(parsers.parse('I release active browser work in session "{session_name}" with marker "{marker}"'))
@when(parsers.parse('I release active work in session "{session_name}" with marker "{marker}"'))
def release_active_browser_work_with_marker(
    client: BaqylauClient,
    sessions: Sessions,
    session_name: str,
    marker: str,
) -> None:
    """Release active browser work with one marker."""
    working_directory = client.sessions.snapshot(sessions.get(session_name)).session_data.session.working_directory
    Path(working_directory, marker).write_text("release\n", encoding="utf-8")
