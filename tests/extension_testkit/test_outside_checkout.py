# Copyright (c) 2026 Zhambyl Yermagambet
"""An extension package runs its host tests outside the checkout with only the installed kit (C26, P08-T01)."""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 -- Run fixed venv, install, and pytest commands without a shell.
import sys
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

import pytest

from tests.extension_host import package_fixture, wheel_fixture

if TYPE_CHECKING:
    from collections.abc import Mapping

ROOT = Path(__file__).resolve().parents[2]
KIT_ROOT = ROOT / "packages" / "extension-testkit"
HOST_EXECUTABLE = ROOT / "bin" / "baqylau-dashboard"
OWNER = "test.outside"
COMMAND_SECONDS = 300
TEST_TIMEOUT_SECONDS = 600
TESTS = "tests"
CLEAN_ENVIRONMENT = MappingProxyType({"PATH": os.defpath})
EXTERNAL_TEST = f"""
import shutil
from importlib.util import find_spec
from pathlib import Path

from baqylau_extension_testkit.data_reads import records
from baqylau_extension_testkit.lifecycle import rescan
from baqylau_extension_testkit.signoff import signoff


def test_private_host(baqylau_host):
    assert find_spec("api") is None and find_spec("sdk") is None
    shutil.copytree(Path(__file__).parent / "package", baqylau_host.roots.packages / "{OWNER}")
    client = baqylau_host.start()
    assert [entry.extension_id for entry in rescan(client).entries] == ["{OWNER}"]
    assert records(client, "{OWNER}", "{OWNER}.notes", '{{"kind":"installation"}}').records == ()
    assert signoff(client).raw_event_count == 0
"""


def run(
    arguments: tuple[str, ...], directory: Path, environment: Mapping[str, str],
) -> str:
    """Run one fixed command and require success.

    Returns:
        The standard output.

    """
    finished = subprocess.run(  # noqa: S603 -- Fixed arguments, no shell.
        arguments, cwd=directory, env=environment, capture_output=True, text=True, check=False,
        timeout=COMMAND_SECONDS,
    )
    assert finished.returncode == 0, finished.stdout + finished.stderr
    return finished.stdout


def installed_kit(directory: Path) -> str:
    """Make a clean venv and install the kit from local wheels only.

    Returns:
        The venv's Python executable.

    """
    build = directory / "build"
    build.mkdir()
    wheels = wheel_fixture.build_wheels(build, KIT_ROOT)
    venv = directory / "venv"
    created = (sys.executable, "-I", "-m", "venv", "--without-pip", str(venv))
    run(created, directory, CLEAN_ENVIRONMENT)
    python = str(venv / "bin" / "python")
    run((
        sys.executable, "-I", "-m", "uv", "pip", "install", "--python", python, "--offline", "--no-index",
        "--no-cache", "--find-links", str(wheels), "baqylau-extension-testkit",
    ), directory, CLEAN_ENVIRONMENT)
    return python


def external_package(directory: Path) -> Path:
    """Write an external package directory with one host test and its web-only package.

    Returns:
        The external package directory.

    """
    external = directory / "external"
    tests = external / TESTS
    tests.mkdir(parents=True)
    (tests / "test_host.py").write_text(EXTERNAL_TEST, encoding="utf-8")
    written = package_fixture.write_package(directory / "fixture", OWNER, web=True)
    shutil.copytree(written, tests / "package")
    return external


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_package_tests_run_with_installed_kit(tmp_path: Path) -> None:
    """A clean venv gets the kit from local wheels; the package's test starts a private host and signs off."""
    python = installed_kit(tmp_path)
    environment = {**CLEAN_ENVIRONMENT, "BAQYLAU_HOST_EXECUTABLE": str(HOST_EXECUTABLE)}
    command = (python, "-I", "-m", "pytest", "-q", "-p", "no:cacheprovider", TESTS)
    output = run(command, external_package(tmp_path), environment)
    assert "1 passed" in output
