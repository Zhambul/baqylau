"""Harness-neutral login-shell command construction."""

from __future__ import annotations

import os

SUPPORTED_LOGIN_SHELLS = frozenset({"bash", "zsh"})


def login_shell_command(command: tuple[str, ...]) -> tuple[str, ...]:
    if not command:
        raise ValueError("launch command cannot be empty")
    shell = os.environ.get("SHELL") or "/bin/zsh"
    if os.path.basename(shell) not in SUPPORTED_LOGIN_SHELLS:
        shell = "/bin/zsh"
    executable, *arguments = command
    return (shell, "-lic", f'{executable} "$@"', executable, *arguments)
