# Copyright (c) 2026 Zhambyl Yermagambet
"""The author example builds, passes the coverage check, and passes its own case through the runner (P08-T05)."""

from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404 -- Run the installed runner module without a shell.
import sys
from pathlib import Path

import pytest

from tests import host_launcher
from tests.extension_host import environment_fixture, wheel_fixture

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "hello-extension"
RUN_SECONDS = 300
TEST_TIMEOUT_SECONDS = 420


def built_example(directory: Path) -> Path:
    """Copy the example and add its wheelhouse and hash-locked requirements, as an author's build does.

    Returns:
        The built package.

    """
    package = directory / "hello-extension"
    shutil.copytree(EXAMPLE, package, ignore=shutil.ignore_patterns("__pycache__"))
    build = directory / "build"
    build.mkdir()
    wheels = wheel_fixture.build_wheels(build)
    shutil.copytree(wheels, package / "wheels")
    (package / "requirements.lock").write_text(environment_fixture.locked_requirements(wheels), encoding="utf-8")
    return package


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_example_passes_its_own_case(tmp_path: Path) -> None:
    """The runner finds complete coverage and the example's case passes against a private host."""
    package = built_example(tmp_path)
    finished = subprocess.run(  # noqa: S603 -- Fixed arguments, no shell.
        (sys.executable, "-I", "-m", "baqylau_extension_testkit.runner", str(package)), cwd=package,
        env={
            "PATH": os.defpath, "HOME": os.environ["HOME"],
            "BAQYLAU_HOST_EXECUTABLE": str(host_launcher.host_executable()),
        },
        capture_output=True, text=True, check=False, timeout=RUN_SECONDS,
    )
    assert finished.returncode == 0, finished.stdout + finished.stderr
    assert "repeatable passed 1, failed 0" in finished.stdout
