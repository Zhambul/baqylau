#!/usr/bin/env python3
"""Send one terminal pane gesture to the daemon.

    terminal_keys.py toggle|grow|shrink|reset|setpct [number]

The terminal's keymap names this file, once per binding, and launches it per
keypress. It observes the two facts only this process can — the window the
keypress landed in and the working directory — and ships them to the daemon's
per-gesture endpoint (the URL is the discriminator, so the body carries no
command word). The gesture itself runs in the daemon
(`terminal/panes/commands.py`).

kitty launches these with `--type=background`, so there is nowhere for a message
to go: a refusal and an unreachable daemon are both silence.
"""

from __future__ import annotations

import os
import sys

from pydantic import BaseModel, ConfigDict

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))  # my own directory

import _daemon                                                   # noqa: E402
import _http                                                     # noqa: E402


class PaneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    window_id: str
    working_directory: str

    def json_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")


class PaneColumnsRequest(PaneRequest):
    columns: int


class PanePercentRequest(PaneRequest):
    percent: int


def request_body(arguments: list[str]) -> PaneRequest:
    command = arguments[0]
    window_id = _http.window_id(os.environ)
    working_directory = os.getcwd()
    if command in ("grow", "shrink") and len(arguments) > 1:
        return PaneColumnsRequest(
            window_id=window_id,
            working_directory=working_directory,
            columns=int(arguments[1]),
        )
    if command == "setpct":
        if len(arguments) != 2:
            raise ValueError("setpct requires one percentage")
        return PanePercentRequest(
            window_id=window_id,
            working_directory=working_directory,
            percent=int(arguments[1]),
        )
    return PaneRequest(
        window_id=window_id,
        working_directory=working_directory,
    )


def main(arguments: list[str]) -> int:
    if not arguments or arguments[0] not in _http.PANE_COMMAND_PATHS:
        print(
            "usage: terminal_keys.py toggle|grow|shrink|reset|setpct [number]",
            file=sys.stderr,
        )
        return 2
    _daemon.post_json(_http.PANE_COMMAND_PATHS[arguments[0]], request_body(arguments))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
