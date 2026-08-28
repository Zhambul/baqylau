"""How a harness CLI is started in a terminal tab.

The configured executable runs as the terminal's foreground process. A shell
is not part of the launch. This keeps interactive terminal access while it
prevents shell startup programs from blocking before the harness starts.
"""

from __future__ import annotations

import re

from terminal.models import TabOpenRequest

ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_launch(
    command: tuple[str, ...], environment: tuple[tuple[str, str], ...]
) -> None:
    if not command:
        raise ValueError("launch command cannot be empty")
    for name, _ in environment:
        if not ENVIRONMENT_NAME.fullmatch(name):
            raise ValueError(f"invalid environment variable name: {name!r}")


def launch_tab_request(
    working_directory: str,
    command: tuple[str, ...],
    title: str = "",
    environment: tuple[tuple[str, str], ...] = (),
) -> TabOpenRequest:
    """Build one direct, interactive terminal launch for a harness CLI."""
    _validate_launch(command, environment)
    return TabOpenRequest(
        working_directory=working_directory,
        command=command,
        title=title,
        environment=environment,
    )
