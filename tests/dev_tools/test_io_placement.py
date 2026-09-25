# Copyright (c) 2026 Zhambyl Yermagambet
"""Process I/O goes through the host, and pure capabilities do no network I/O (P02-T04)."""

from pathlib import Path

import pytest

from tests.dev_tools import package_fixture as fixtures

ENCODING = "utf-8"
PROJECTOR = '''# Copyright (c) 2026 Zhambyl Yermagambet
"""Keep one pure projector beside a network import."""

import socket

from baqylau_extension_api.contracts.projection import ExtensionProjector


class Cards(ExtensionProjector):
    """Draw no cards."""


HOST = socket.gethostname
'''
PROCESS = '''# Copyright (c) 2026 Zhambyl Yermagambet
"""Run a program without the host."""

import subprocess

RUN = subprocess.run
'''
LIVE = '''# Copyright (c) 2026 Zhambyl Yermagambet
"""Read a URL from live code, where the host bounds the call."""

from urllib.parse import urljoin

JOIN = urljoin
'''


def with_module(directory: Path, name: str, text: str) -> None:
    """Add one module to the fixture package and list it as a module of the package."""
    fixtures.create_package(directory)
    (directory / "src" / f"{name}.py").write_text(text, encoding=ENCODING)
    project = directory / "pyproject.toml"
    listed = project.read_text(encoding=ENCODING)
    project.write_text(listed.replace('"feature_backend"]', f'"feature_backend", "{name}"]'), encoding=ENCODING)


@pytest.mark.parametrize(("text", "reason"), [
    (PROJECTOR, "imports socket; move network work to a live capability"),
    (PROCESS, "run programs through the host process service"),
])
def test_unbounded_io_is_refused(tmp_path: Path, text: str, reason: str) -> None:
    """A network import beside a projector, and a process import anywhere, fail the gate."""
    with_module(tmp_path, "extra_module", text)

    completed = fixtures.invoke(tmp_path, "deadcode")

    assert completed.returncode != 0
    assert reason in completed.stdout + completed.stderr


def test_live_network_use_is_accepted(tmp_path: Path) -> None:
    """A module with no pure capability may use the network libraries."""
    with_module(tmp_path, "extra_module", LIVE)

    completed = fixtures.invoke(tmp_path, "deadcode")

    assert "imports urllib" not in completed.stdout + completed.stderr
