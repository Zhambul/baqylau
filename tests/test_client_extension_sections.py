# Copyright (c) 2026 Zhambyl Yermagambet
"""The mirror paints extension sections; the scoreboard shows one chip for each (P07-T02)."""

from __future__ import annotations

import re

from tests import client_test_servers, test_client_loading

sections = test_client_loading.load_shared("_extension_sections")
terminal = test_client_loading.load_shared("_model_terminal")
daemon = client_test_servers.daemon
ANSI = re.compile(r"\x1b\[[0-9;]*m")
WIDTH = 60
REPLY_JSON = (
    b'{"views":[{"title":"Deploy","blocks":[{"kind":"status","block_id":"s","label":"Healthy","tone":"success"}]},'
    b'{"title":"Notes","blocks":[]}],"unavailable":["Logs"]}'
)
REPLY = terminal.PaneSectionsReply.model_validate_json(REPLY_JSON)


def test_mirror_paints_sections_and_failures() -> None:
    """Every section is painted in full, and a failed view has one line."""
    rows = sections.mirror_rows(REPLY, WIDTH)
    painted = [ANSI.sub("", row).rstrip() for row in rows]

    assert "Deploy" in painted and "Notes" in painted
    assert "● Healthy" in painted
    assert "Logs is not available." in painted


def test_scoreboard_uses_status_or_title() -> None:
    """A chip is the view's first status, else its title; a failed view is a red chip."""
    chips = [chip.text for chip in sections.score_chips(REPLY)]

    assert chips == ["Deploy: Healthy", "Notes", "Logs unavailable"]


def test_cache_keeps_sections_when_daemon_stops(daemon: client_test_servers._Capture) -> None:
    """A reply is kept for later paints; a changed width reads again."""
    daemon.replies["/api/extension-terminal/sessions/"] = REPLY_JSON
    cache = sections.SectionCache("127.0.0.1", daemon.port, "session one", "mirror")

    first = cache.current(WIDTH)
    cache.port = client_test_servers.free_port()
    again = cache.current(WIDTH + 1)

    assert [view.title for view in first.views] == ["Deploy", "Notes"]
    assert again == first
    assert "/sessions/session%20one/mirror?columns=60" in daemon.delivery("/api/extension-terminal/sessions/").path
