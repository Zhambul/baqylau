"""How a harness CLI is started in a terminal tab.

Not a presenter: this is the launch CONVENTION every caller that opens a tab
for a CLI shares — the command runs under a login shell, because a harness CLI
is routinely a shell alias or a function that only exists once the user's own
shell has been initialised.
"""

from __future__ import annotations

import os
import re
import shlex

from terminal.models import TabOpenRequest

SUPPORTED_LOGIN_SHELLS = frozenset({"bash", "zsh"})
ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def login_shell_command(
    command: tuple[str, ...],
    environment: tuple[tuple[str, str], ...] = (),
) -> tuple[str, ...]:
    if not command:
        raise ValueError("launch command cannot be empty")
    for name, _ in environment:
        if not ENVIRONMENT_NAME.fullmatch(name):
            raise ValueError(f"invalid environment variable name: {name!r}")
    shell = os.environ.get("SHELL") or "/bin/zsh"
    if os.path.basename(shell) not in SUPPORTED_LOGIN_SHELLS:
        shell = "/bin/zsh"
    executable, *arguments = command
    # Assignments precede the command word INSIDE the -c string, so the
    # executable still sits in command position and shell aliases resolve.
    assignments = "".join(
        f"{name}={shlex.quote(value)} " for name, value in environment
    )
    return (shell, "-lic", f'{assignments}{executable} "$@"', executable, *arguments)


def launch_tab_request(
    working_directory: str,
    command: tuple[str, ...],
    title: str = "",
    environment: tuple[tuple[str, str], ...] = (),
) -> TabOpenRequest:
    """The tab request for running a harness CLI — the one construction site of
    the login-shell convention, shared by every caller that starts one."""
    return TabOpenRequest(
        working_directory=working_directory,
        command=login_shell_command(command, environment),
        title=title,
    )
