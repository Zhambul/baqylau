# Copyright (c) 2026 Zhambyl Yermagambet
"""Install the native plugin through OpenCode's plugin discovery directory."""

import os
from pathlib import Path


def install(configuration_directory: Path) -> Path:
    """Link this package without changing the user's configuration document.

    Returns:
        The installed plugin path.

    Raises:
        FileExistsError: If another file owns the installation path.

    """
    package = Path(__file__).resolve().parent
    destination = configuration_directory / "plugins" / "baqylau"
    if destination.is_symlink() and destination.resolve() == package:
        return destination
    if destination.exists() or destination.is_symlink():
        message = f"Another file owns the plugin path: {destination}"
        raise FileExistsError(message)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.symlink_to(package, target_is_directory=True)
    return destination


def main() -> None:
    """Install the plugin for normal terminal launches."""
    default_directory = str(Path.home() / ".config")
    configuration_home = Path(os.environ.get("XDG_CONFIG_HOME", default_directory))
    native_directory = os.environ.get("OPENCODE_CONFIG_DIR")
    install(Path(native_directory) if native_directory else configuration_home / "opencode")


if __name__ == "__main__":
    main()
