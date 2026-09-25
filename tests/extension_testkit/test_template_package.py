# Copyright (c) 2026 Zhambyl Yermagambet
"""A new package with both views passes its own case through the kit runner (P02-T05)."""

from __future__ import annotations

import os
import subprocess  # noqa: S404 -- Run the installed modules without a shell.
import sys
from pathlib import Path

import pytest

from tests.extension_host import wheel_fixture

ROOT = Path(__file__).resolve().parents[2]
HOST_EXECUTABLE = ROOT / "bin" / "baqylau-dashboard"
RUN_SECONDS = 300
TEST_TIMEOUT_SECONDS = 420


def created(directory: Path) -> Path:
    """Create a new package with both views.

    Returns:
        The package directory.

    """
    package = directory / "package"
    new = ("baqylau_dev", "new", "--root", str(package), "--id", "example.fresh", "--web", "--terminal")
    subprocess.run(  # noqa: S603 -- The current Python with fixed module arguments.
        (sys.executable, "-m", *new), check=True, capture_output=True, timeout=RUN_SECONDS,
    )
    return package


def runner_environment(directory: Path) -> dict[str, str]:
    """Give the runner the host executable and a built SDK wheel, and nothing from this checkout.

    Returns:
        The environment.

    """
    build = directory / "build"
    build.mkdir()
    sdk_wheel = next(wheel_fixture.build_wheels(build).glob("baqylau_extension_api-*.whl"))
    return {
        "PATH": os.defpath, "HOME": os.environ["HOME"],
        "BAQYLAU_HOST_EXECUTABLE": str(HOST_EXECUTABLE), "BAQYLAU_SDK_WHEEL": str(sdk_wheel),
    }


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_template_passes_its_own_case(tmp_path: Path) -> None:
    """The runner finds complete coverage for the worker, API, web, and terminal surfaces, and the case passes."""
    package = created(tmp_path)

    finished = subprocess.run(  # noqa: S603 -- Fixed arguments, no shell.
        (sys.executable, "-I", "-m", "baqylau_extension_testkit.runner", str(package)), cwd=package,
        env=runner_environment(tmp_path), capture_output=True, text=True, check=False, timeout=RUN_SECONDS,
    )

    assert finished.returncode == 0, finished.stdout + finished.stderr
    assert "repeatable passed 1, failed 0" in finished.stdout
