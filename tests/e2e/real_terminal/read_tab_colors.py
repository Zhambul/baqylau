"""Read one Kitty tab's applied color overrides for real-terminal E2E tests."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar, cast

from kittens.tui.handler import result_handler as _result_handler  # type: ignore[import-not-found]

Handler = TypeVar("Handler", bound=Callable[..., object])
result_handler = cast(
    Callable[..., Callable[[Handler], Handler]],
    _result_handler,
)


class Window(Protocol):
    def tabref(self) -> object: ...


class Boss(Protocol):
    window_id_map: Mapping[int, Window]


def main(_args: list[str]) -> None:
    """The remote-control handler does all work without a user interface."""


@result_handler(no_ui=True)
def handle_result(
    _args: list[str],
    _result: object,
    target_window_id: int,
    boss: Boss,
) -> str:
    window = boss.window_id_map[target_window_id]
    tab = window.tabref()
    names = (
        "active_bg",
        "active_fg",
        "inactive_bg",
        "inactive_fg",
    )
    return json.dumps({name: getattr(tab, name, None) for name in names})
