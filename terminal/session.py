"""Harness-neutral login-shell command construction."""

from __future__ import annotations

import os
import re
import shlex

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
