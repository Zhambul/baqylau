"""Read the real color overrides that Kitty applied to one session tab."""

from __future__ import annotations

import json
from pathlib import Path

from sdk.client import wait_for
from terminal.impl.kitty.remote import KittyRemote
from terminal.models import RGB, TabAppearance
from terminal.models.values import WindowId

COLOR_KITTEN = Path(__file__).resolve().parents[1] / "real_terminal" / "read_tab_colors.py"


def _value(color: RGB) -> int:
    return (color.red << 16) | (color.green << 8) | color.blue


class KittyTabColorReader:
    """A read-only E2E probe for Kitty's stored tab color overrides."""

    def __init__(self, remote: KittyRemote | None = None) -> None:
        self._remote = remote or KittyRemote()

    def wait_for(self, window_id: str, expected: TabAppearance, timeout: float) -> None:
        wanted = {
            "active_bg": _value(expected.active_background),
            "active_fg": _value(expected.active_foreground),
            "inactive_bg": _value(expected.inactive_background),
            "inactive_fg": _value(expected.inactive_foreground),
        }

        def matches() -> bool | None:
            found = self._read(WindowId(window_id))
            return True if found == wanted else None

        wait_for(
            f"Kitty tab {window_id!r} to have colors {wanted}",
            matches,
            timeout=timeout,
        )

    def _read(self, window_id: WindowId) -> dict[str, int | None] | None:
        output = self._remote.capture(
            "kitten",
            "--match",
            f"id:{window_id}",
            str(COLOR_KITTEN),
        )
        if output is None:
            return None
        value = json.loads(output)
        if not isinstance(value, dict):
            return None
        return {
            name: item if isinstance(item, int) else None
            for name, item in value.items()
            if isinstance(name, str)
        }
