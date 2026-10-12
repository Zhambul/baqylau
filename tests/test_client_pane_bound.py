# Copyright (c) 2026 Zhambyl Yermagambet
"""A Kitty pane keeps only the feed that its paint shows."""

from __future__ import annotations

from tests import client_test_models, test_client_loading

WIDTH = 80
SHELL_ID = "shell-1"
MESSAGE_LINES = 40


def _message(entry_id: int) -> client_test_models.JsonValue:
    body: dict[str, client_test_models.JsonValue] = {
        "role": "assistant", "phase": "end_turn", "content": {"text": f"message {entry_id}\n" * MESSAGE_LINES},
    }
    return client_test_models.lead_pane_entry(str(entry_id), "message", body)


def _more_than_the_scrollback() -> list[client_test_models.JsonValue]:
    scrollback = test_client_loading.load_shared("_render_styles").SCROLLBACK_ROWS
    message_count = scrollback // MESSAGE_LINES * 2
    return [_message(entry_id) for entry_id in range(1, message_count)]


def _shell_part(entry_id: str, kind: str) -> client_test_models.JsonValue:
    body: dict[str, client_test_models.JsonValue] = {"shell_id": SHELL_ID}
    if kind == "shell_started":
        body |= {"command": {"text": "make test"}, "execution": "foreground"}
    else:
        body |= {"stream": "output", "mode": "append", "content": {"text": "late output\n"}}
    return client_test_models.lead_pane_entry(entry_id, kind, body)


def test_paint_shows_only_the_scrollback() -> None:
    """The paint stops when the scrollback is full, and the forgotten items do not change the screen."""
    model = test_client_loading.load_shared("_model").SessionModel()
    render = test_client_loading.load_shared("_render")
    messages = _more_than_the_scrollback()
    model.apply_page({"items": messages})
    screen = render.mirror(model, WIDTH)

    painted = render.mirror_rows(model, WIDTH)
    model.keep_newest(painted.shown)

    assert painted.shown < len(messages)
    assert len(list(model.feed())) == painted.shown
    assert render.mirror(model, WIDTH) == screen


def test_forgotten_shell_stays_forgotten() -> None:
    """A shell that the pane forgot does not come back with its next output."""
    model = test_client_loading.load_shared("_model").SessionModel()
    model.apply_page({"items": [_shell_part("1", "shell_started")]})

    model.keep_newest(0)
    model.apply_frame({"entries": [_shell_part("2", "shell_output")]})

    assert list(model.feed()) == []


def test_an_overlapping_shell_part_folds_once() -> None:
    """A frame that sends a shell part again does not add its output twice."""
    model = test_client_loading.load_shared("_model").SessionModel()
    parts = [_shell_part("1", "shell_started"), _shell_part("2", "shell_output")]
    model.apply_page({"items": parts})

    model.apply_frame({"entries": parts})

    assert [shell.output for shell in model.feed()] == ["late output\n"]
