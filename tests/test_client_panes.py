# Copyright (c) 2026 Zhambyl Yermagambet
"""Verify terminal client architecture and behavior."""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess  # noqa: S404 -- Test the terminal pane as a child process.
from pathlib import Path
from typing import TYPE_CHECKING

from core import clients
from tests import (
    client_test_models,
    client_test_process_output,
    client_test_servers,
    test_client_loading,
    test_client_programs,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

TERMINAL_PANE = "terminal_pane.py"


TERMINAL_VIEW = "terminal_view.py"


TERMINAL_CONTENT = "terminal_content.py"


HANDOFF_KIND = "mirror"


EXECUTABLE_FILE_MODE = 0o755


STANDARD_RENDER_WIDTH = 80


TYPE_FIELD = "type"


CURSOR_FIELD = "cursor"


ACTOR_ID_FIELD = "actor_id"


LEAD_ACTOR_ID_TEXT = "lead"


CONTENT_FIELD = "content"


TEXT_FIELD = "text"


STATE_FIELD = "state"


SESSION_FIELD = "session"


SESSION_ID_FIELD = "session_id"


TEST_SESSION_ID_TEXT = "session-one"


LEAD_ACTOR_ID_FIELD = "lead_actor_id"


ACCOUNT_FIELD = "account"


ACTORS_FIELD = "actors"


LIVE_FIELD = "live"


TEXT_ENCODING = "utf-8"


MODEL_MODULE_NAME = "_model"


RENDER_MODULE_NAME = "_render"


ITEMS_FIELD = "items"


SHELL_ID_FIELD = "shell_id"


FIRST_SHELL_ID = "sh-1"


SHELL_COMMAND = "make test"


SHELL_OUTPUT_TYPE = "shell_output"


STREAM_FIELD = "stream"


OUTPUT_STREAM = "output"


MODE_FIELD = "mode"


TASKS_FIELD = "tasks"


def _install_fake_clipboard(tmp_path: Path) -> None:
    clipboard = tmp_path / "pbcopy"
    clipboard.write_text(f"#!/bin/sh\ncat > {tmp_path}/copied\n", encoding=TEXT_ENCODING)
    clipboard.chmod(EXECUTABLE_FILE_MODE)


def _clean_handoff_paths() -> tuple[str, ...]:
    handoff_paths = test_client_loading.load_shared("_handoff_paths").handoff_paths
    paths = (
        handoff_paths.pane_path(TEST_SESSION_ID_TEXT, HANDOFF_KIND),
        handoff_paths.view_path(TEST_SESSION_ID_TEXT, HANDOFF_KIND),
        handoff_paths.lock_path(TEST_SESSION_ID_TEXT, HANDOFF_KIND),
    )
    for pane_path in paths:
        Path(pane_path).unlink(missing_ok=True)
    return paths


def _register_handoff_cleanup(
    cleanup: contextlib.ExitStack,
    paths: Sequence[str],
) -> None:
    for pane_path in paths:
        cleanup.callback(Path(pane_path).unlink, missing_ok=True)


def test_copy_and_expand_handlers_reach_pane(daemon: client_test_servers._Capture, tmp_path: Path) -> None:
    """Both click gestures are FRONTEND-ONLY, and this is what that means.

    The pane holds every byte it draws — content is embedded in the entries it
    was served — so a copy needs no route and an expansion needs no stored state.
    The two programs the terminal launches for a click talk to the pane through a
    local file (client/_handoff.py) and the daemon is not involved in either. The
    stub daemon here is a WITNESS: it must record no delivery at all.
    """
    handoff = test_client_loading.load_shared("_handoff")
    # A fake `pbcopy` on PATH: the real one would reach the machine's clipboard,
    # and a test may not.
    _install_fake_clipboard(tmp_path)

    # From a clean slate: an expansion deliberately OUTLIVES the pane that drew
    # it (the next pane on this session opens with the reader's own expansions in
    # place), so a leftover file from an earlier run would answer for this one.
    paths = _clean_handoff_paths()

    # The pane's half of the channel, written the way the pane writes it.
    handoff.publish(TEST_SESSION_ID_TEXT, HANDOFF_KIND, {"sh:sh-1:out": "445 passed"})
    with contextlib.ExitStack() as cleanup:
        _register_handoff_cleanup(cleanup, paths)
        content = test_client_programs.run_client(
            TERMINAL_CONTENT,
            [f"baqylau-content://{TEST_SESSION_ID_TEXT}/{HANDOFF_KIND}/sh:sh-1:out"],
            port=daemon.port,
            environment={"PATH": "{}:{}".format(tmp_path, os.environ.get("PATH", ""))},
        )
        view = test_client_programs.run_client(
            TERMINAL_VIEW,
            [f"baqylau-view://{TEST_SESSION_ID_TEXT}/{HANDOFF_KIND}/entry-9"],
            port=daemon.port,
        )

        assert (content.returncode, view.returncode) == (0, 0)
        assert (tmp_path / "copied").read_bytes() == b"445 passed"
        # The expansion is recorded where the PANE will read it…
        assert handoff.opened(TEST_SESSION_ID_TEXT, HANDOFF_KIND) == frozenset(("entry-9",))
        # …and toggling the same entry again puts it back.
        test_client_programs.run_client(
            TERMINAL_VIEW,
            [f"baqylau-view://{TEST_SESSION_ID_TEXT}/{HANDOFF_KIND}/entry-9"],
            port=daemon.port,
        )
        assert handoff.opened(TEST_SESSION_ID_TEXT, HANDOFF_KIND) == frozenset()
        # Neither gesture asked the daemon anything.
        assert not daemon.deliveries


def test_pane_publishes_what_click_can_copy() -> None:
    """The pane's half of both gestures, without a process.

    `copy_targets` is built from the MODEL rather than collected while painting,
    so what a click can reach is exactly what the feed holds — and the expanded
    set is an argument to the paint, so an expansion is a repaint and nothing
    more.
    """
    model_module = test_client_loading.load_shared(MODEL_MODULE_NAME)
    render = test_client_loading.load_shared(RENDER_MODULE_NAME)

    model = model_module.SessionModel()
    model.apply_snapshot(
        {
            CURSOR_FIELD: 1,
            SESSION_FIELD: {
                SESSION_ID_FIELD: TEST_SESSION_ID_TEXT,
                LEAD_ACTOR_ID_FIELD: LEAD_ACTOR_ID_TEXT,
                ACCOUNT_FIELD: None,
            },
            ACTORS_FIELD: [{ACTOR_ID_FIELD: "kid", "name": "Explore", "background": {}}],
            LIVE_FIELD: True,
        },
    )

    model.apply_page(
        {
            ITEMS_FIELD: [
                client_test_models.child_pane_entry(
                    "1",
                    "shell_started",
                    {
                        SHELL_ID_FIELD: FIRST_SHELL_ID,
                        "command": {TEXT_FIELD: SHELL_COMMAND},
                        "execution": "foreground",
                    },
                ),
                client_test_models.child_pane_entry(
                    "2",
                    SHELL_OUTPUT_TYPE,
                    {
                        SHELL_ID_FIELD: FIRST_SHELL_ID,
                        STREAM_FIELD: OUTPUT_STREAM,
                        MODE_FIELD: "append",
                        CONTENT_FIELD: {TEXT_FIELD: "445 passed\n"},
                    },
                ),
                client_test_models.child_pane_entry(
                    "3",
                    "file",
                    {
                        "path": "domain/entries.py",
                        "action": "updated",
                        STATE_FIELD: "succeeded",
                        "lines_added": 1,
                        "lines_removed": 1,
                        CONTENT_FIELD: {TEXT_FIELD: "@@ -7 +7 @@\n-old line\n+new line\n"},
                    },
                ),
            ],
        },
    )

    # Both halves of the command, under the names a link carries.
    assert render.copy_targets(model) == {
        "sh:sh-1:cmd": SHELL_COMMAND,
        "sh:sh-1:out": "445 passed\n",
    }

    assert (
        "new line" not in render.mirror(
            model,
            STANDARD_RENDER_WIDTH,
            view=lambda entry_id: f"view://{entry_id}",
        )
        and "view://3" in render.mirror(
            model,
            STANDARD_RENDER_WIDTH,
            view=lambda entry_id: f"view://{entry_id}",
        )
    )

    opened = render.mirror(
        model,
        STANDARD_RENDER_WIDTH,
        view=lambda entry_id: f"view://{entry_id}",
        opened=frozenset(("3",)),
    )
    plain_opened = re.sub(r"\033\[[0-9;]*m", "", opened)
    assert "new line" in plain_opened and "old line" in plain_opened and "@@ -7 +7 @@" not in plain_opened
    assert all(
        colour in opened
        for colour in (
            "\033[48;2;55;31;36m",
            "\033[48;2;29;50;38m",
            "\033[48;2;103;42;50mold",
            "\033[48;2;43;87;58mnew",
        )
    )
    # The diff came from the ENTRY. There is no second request and no route left
    # to make one to.
    assert SHELL_COMMAND in opened


def test_pane_renders_read_model_and_never_sends(
    daemon: client_test_servers._Capture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a pane renders the read model and never sends a width.

    A pane is told its address, its session and its kind — everything it
        cannot observe — and draws everything else itself.

        This is the whole shape of the redesign in one test: the daemon answers with
        FACTS, and the ANSI is the client's. The width never crosses the socket,
        which is what makes a resize a repaint instead of a reconnect.
    """
    monkeypatch.setattr(clients, "PORT_NUMBER", daemon.port)
    daemon.replies = {
        "/sessionData/session-one/entries": json.dumps(
            {
                ITEMS_FIELD: [
                    {
                        "entry_id": "e1",
                        TYPE_FIELD: "shell_started",
                        CURSOR_FIELD: 4,
                        ACTOR_ID_FIELD: LEAD_ACTOR_ID_TEXT,
                        "parent_actor_id": None,
                        "turn_id": None,
                        "occurred_at": 1.0,
                        "summary": None,
                        "body": {
                            SHELL_ID_FIELD: FIRST_SHELL_ID,
                            "command": {TEXT_FIELD: SHELL_COMMAND, "media_type": "text/plain"},
                            "execution": "foreground",
                        },
                    },
                ],
                "oldest_cursor": 4,
                "has_more": False,
            },
        ).encode(TEXT_ENCODING),
        "/sessionData/session-one": json.dumps(
            {
                CURSOR_FIELD: 4,
                SESSION_FIELD: {
                    SESSION_ID_FIELD: TEST_SESSION_ID_TEXT,
                    "harness": "claude_code",
                    "title": None,
                    STATE_FIELD: "running",
                    "working_directory": "/test-data",
                    "started_at": 1.0,
                    "finished_at": None,
                    ACCOUNT_FIELD: None,
                    LEAD_ACTOR_ID_FIELD: LEAD_ACTOR_ID_TEXT,
                    "goal": None,
                    TASKS_FIELD: [],
                },
                ACTORS_FIELD: [],
                LIVE_FIELD: True,
                "repository": None,
            },
        ).encode(TEXT_ENCODING),
    }
    # One frame, then the connection ends: the command finishes, and what the
    # pane draws has to reflect the frame rather than the page it started from.
    daemon.stream = (
        "event: sessionData\ndata: "
        + json.dumps(
            {
                "entries": [
                    {
                        "entry_id": "e2",
                        TYPE_FIELD: "shell_finished",
                        CURSOR_FIELD: 5,
                        ACTOR_ID_FIELD: LEAD_ACTOR_ID_TEXT,
                        "parent_actor_id": None,
                        "turn_id": None,
                        "occurred_at": 3.0,
                        "summary": None,
                        "body": {SHELL_ID_FIELD: FIRST_SHELL_ID, STATE_FIELD: "failed", "exit_code": 2},
                    },
                ],
            },
        )
        + "\n\n"
    )
    process = subprocess.Popen(  # noqa: S603 -- Run the fixed terminal-pane client and test arguments without a shell.
        clients.command(TERMINAL_PANE, TEST_SESSION_ID_TEXT, "mirror"),
        stdout=subprocess.PIPE,
        env={**os.environ, "COLUMNS": "100", "LINES": "40"},
    )
    with contextlib.ExitStack() as cleanup:
        cleanup.callback(process.wait, timeout=10)
        cleanup.callback(process.terminate)
        painted = client_test_process_output.read_until_marker(process, "failed (exit 2)")

    assert SHELL_COMMAND in painted  # the page, drawn here
    assert "failed (exit 2)" in painted  # and then the frame
    assert "\x1b[" in painted  # as ANSI, from this process
    _assert_pane_requests(daemon)


def _assert_pane_requests(daemon: client_test_servers._Capture) -> None:
    """Verify the pane read and stream requests."""
    assert daemon.delivery("/sessionData/session-one/entries").path == ("/sessionData/session-one/entries?at=4")
    assert daemon.delivery("/stream").path == (
        "/sessionData/session-one/stream?after_cursor=4&after_entry=4&include_application=false"
    )
    assert not any("width" in found.path for found in daemon.deliveries)
