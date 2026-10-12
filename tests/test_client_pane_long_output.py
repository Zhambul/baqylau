# Copyright (c) 2026 Zhambyl Yermagambet
"""A long shell output costs a Kitty pane only the rows that its scrollback keeps."""

from __future__ import annotations

from tests import client_test_models, test_client_loading

WIDTH = 80
SHELL_ID = "shell-1"
# Each third line ends with a carriage return and a line feed. `splitlines` reads them as one line end.
LINE_ENDS = ("\n", "\n", "\r\n")


def _shell_model(output: str) -> object:
    started: dict[str, client_test_models.JsonValue] = {
        "shell_id": SHELL_ID, "command": {"text": "make test"}, "execution": "foreground",
    }
    written: dict[str, client_test_models.JsonValue] = {
        "shell_id": SHELL_ID, "stream": "output", "mode": "append", "content": {"text": output},
    }
    model = test_client_loading.load_shared("_model").SessionModel()
    model.apply_page({"items": [
        client_test_models.lead_pane_entry("1", "shell_started", started),
        client_test_models.lead_pane_entry("2", "shell_output", written),
    ]})
    return model


def _output(first: int, last: int) -> str:
    parts: list[str] = []
    for number in range(first, last + 1):
        parts.extend((f"line {number}", LINE_ENDS[number % len(LINE_ENDS)]))
    return "".join(parts)


def test_paint_does_not_grow_with_the_output() -> None:
    """An output longer than the scrollback paints only the rows that the scrollback keeps."""
    render = test_client_loading.load_shared("_render")
    scrollback = test_client_loading.load_shared("_render_styles").SCROLLBACK_ROWS

    painted = render.mirror_rows(_shell_model(_output(1, scrollback * 3)), WIDTH)

    assert scrollback <= len(painted.rows) < scrollback * 2


def test_paint_does_not_grow_with_one_long_line() -> None:
    """One line longer than the scrollback paints only the rows that the scrollback keeps, and its end."""
    render = test_client_loading.load_shared("_render")
    scrollback = test_client_loading.load_shared("_render_styles").SCROLLBACK_ROWS
    words = [f"w{number}" for number in range(scrollback * WIDTH // 3)]

    painted = render.mirror_rows(_shell_model(" ".join(words)), WIDTH)

    assert scrollback <= len(painted.rows) < scrollback * 2
    assert words[-1] in painted.rows[-1]


def test_long_output_screen_is_its_newest_lines() -> None:
    """The lines that the paint does not read have no row on the screen."""
    scrollback = test_client_loading.load_shared("_render_styles").SCROLLBACK_ROWS
    last = scrollback * 3

    full = _screen(1, last)

    assert full == _screen(last - scrollback + 1, last)


def _screen(first: int, last: int) -> str:
    render = test_client_loading.load_shared("_render")
    return str(render.mirror(_shell_model(_output(first, last)), WIDTH))
