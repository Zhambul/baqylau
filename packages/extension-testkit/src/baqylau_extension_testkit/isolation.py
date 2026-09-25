# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep a test host away from the user's port, data, configuration, and Keychain."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

# The port of the user's own dashboard.
USER_PORT = 8377
# The data directory that the host uses when nothing else is set.
USER_DATA = Path("~/.local/share/baqylau").expanduser()
# Only these values come from the caller; every other value is private to the test.
INHERITED_VARIABLES = ("PATH", "LANG", "LC_ALL", "TMPDIR")
FIXED_VARIABLES = MappingProxyType({
    "BAQYLAU_TERMINAL": "none",
    "BAQYLAU_DASHBOARD_NOTIFY_TELEGRAM": "0",
    "BAQYLAU_DASHBOARD_NOTIFY_WEBPUSH": "0",
    # The keyring library refuses every secret operation with this backend.
    "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
})


class UnsafeFixtureError(ValueError):
    """Refuse a fixture value that can reach the user's own host or data."""


@dataclass(frozen=True)
class PrivateRoots:
    """Name the private directories of one test host under one test directory."""

    root: Path

    @property
    def data_directory(self) -> Path:
        """Give the host data directory."""
        return self.root / "data"

    @property
    def packages(self) -> Path:
        """Give the extension root that the host scans."""
        return self.root / "packages"

    @property
    def workspace(self) -> Path:
        """Give a private workspace for sessions of the test."""
        return self.root / "workspace"

    @property
    def home(self) -> Path:
        """Give the private home, configuration, and notification directory."""
        return self.root / "home"

    @property
    def log(self) -> Path:
        """Give the host's own output file."""
        return self.root / "host.log"

    def create(self) -> None:
        """Make every directory, and refuse the user's data directory."""
        check_data(self.data_directory)
        for directory in (self.data_directory, self.packages, self.workspace, self.home):
            directory.mkdir(parents=True, exist_ok=True)

    def environment(self) -> dict[str, str]:
        """Build the complete child environment.

        Returns:
            The few inherited values, the fixed values, and private homes.

        """
        inherited = {name: os.environ[name] for name in INHERITED_VARIABLES if name in os.environ}
        private = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
            "CODEX_HOME": str(self.home / ".codex"),
            "OPENCODE_CONFIG_DIR": str(self.home / ".config" / "opencode"),
            "BAQYLAU_DASHBOARD_TELEGRAM_DIR": str(self.home / "telegram"),
        }
        return {**inherited, **FIXED_VARIABLES, **private}


def check_port(port: int) -> int:
    """Refuse the user's dashboard port.

    Returns:
        The same port.

    Raises:
        UnsafeFixtureError: If the port is the user's dashboard port.

    """
    if port == USER_PORT:
        message = f"port {USER_PORT} belongs to the user's dashboard"
        raise UnsafeFixtureError(message)
    return port


def check_data(directory: Path) -> Path:
    """Refuse the user's data directory and every directory in it.

    Returns:
        The same directory.

    Raises:
        UnsafeFixtureError: If the directory is the user's data directory or in it.

    """
    resolved = directory.expanduser().resolve()
    if resolved.is_relative_to(USER_DATA.resolve()):
        message = f"{directory} is in the user's data directory"
        raise UnsafeFixtureError(message)
    return directory


def free_port() -> int:
    """Ask the system for a free local port.

    Returns:
        A free port that is not the user's dashboard port.

    """
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port != USER_PORT:
            return port
