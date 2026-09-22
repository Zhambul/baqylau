# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep normal and test extension roots explicit across CLI and process boundaries."""

import os
from pathlib import Path

import pytest

from api.runtime import ApplicationConfig
from dashboard import cli_forwarding, cli_options
from dashboard.cli_option_values import EXTENSION_ROOT_FLAG
from dashboard.cli_output import UsageError
from extensions.configuration import ROOTS_ENVIRONMENT, configured_roots


def test_default_roots_use_private_data_directory(tmp_path: Path) -> None:
    """An explicit test data directory must not inherit the user's extension roots."""
    config = ApplicationConfig(data_directory=tmp_path, base_environment={ROOTS_ENVIRONMENT: "/user/packages"})
    environment = config.process_environment()
    assert environment[ROOTS_ENVIRONMENT] == str(tmp_path / "extensions")
    assert not (tmp_path / "extensions").exists()


def test_explicit_empty_roots_disable_discovery(tmp_path: Path) -> None:
    """An empty tuple is distinct from the default package root."""
    config = ApplicationConfig(data_directory=tmp_path, extension_roots=())
    environment = config.process_environment()
    assert not environment[ROOTS_ENVIRONMENT]
    assert not configured_roots(environment, tmp_path).directories


def test_environment_roots_are_normalized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeated roots do not produce duplicate catalog rows."""
    root = tmp_path / "with spaces"
    monkeypatch.setenv("BAQYLAU_DATA_DIR", str(tmp_path))
    repeated = os.pathsep.join((str(root), str(root)))
    monkeypatch.setenv(ROOTS_ENVIRONMENT, repeated)
    config = ApplicationConfig.from_environment()
    assert config.extension_roots == (root,)
    assert config.process_environment()[ROOTS_ENVIRONMENT] == str(root)


def test_cli_forwards_repeated_extension_roots(tmp_path: Path) -> None:
    """Both option forms reach the child daemon without losing spaces or a root."""
    first, second = tmp_path / "one", tmp_path / "second root"
    arguments = [EXTENSION_ROOT_FLAG, str(first), f"{EXTENSION_ROOT_FLAG}={second}"]
    options = cli_options.launch_options(arguments)
    roots = os.pathsep.join((str(first), str(second)))
    assert options.variables[ROOTS_ENVIRONMENT] == roots
    assert cli_forwarding.forwarded_flags(arguments) == [
        EXTENSION_ROOT_FLAG, str(first), EXTENSION_ROOT_FLAG, str(second),
    ]


def test_separator_in_root_is_rejected(tmp_path: Path) -> None:
    """A path cannot silently turn into two roots when it crosses the environment."""
    root = tmp_path / f"part{os.pathsep}other"
    with pytest.raises(UsageError, match="separator"):
        cli_options.launch_options([EXTENSION_ROOT_FLAG, str(root)])
    config = ApplicationConfig(data_directory=tmp_path, extension_roots=(root,))
    with pytest.raises(ValueError, match="separator"):
        config.process_environment()
