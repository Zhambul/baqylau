# Copyright (c) 2026 Zhambyl Yermagambet
"""The runner reads the host's harnesses, refuses missing coverage, and reports live cases apart (P08-T02)."""

from __future__ import annotations

import subprocess  # noqa: S404 -- Run the installed runner module without a shell.
import sys
from pathlib import Path
from typing import Final

import pytest
from baqylau_extension_api.manifest.e2e import E2eCase, HarnessLimit
from baqylau_extension_testkit.runner import COVERAGE_FAILED

from tests.extension_host import package_fixture

ROOT = Path(__file__).resolve().parents[2]
HOST_EXECUTABLE = ROOT / "bin" / "baqylau-dashboard"
OWNER = "test.runner"
CASE_PATH = "tests/e2e/test_web.py"
WEB: Final = "web"
CASES = b"""import pytest


def test_repeatable():
    assert True


@pytest.mark.baqylau_live
def test_live_terminal():
    assert False, "a live case does not run without --live"
"""
RUN_SECONDS = 120
TEST_TIMEOUT_SECONDS = 180


def runner_output(package: Path) -> subprocess.CompletedProcess[str]:
    """Run the runner module on one package with the checkout's host executable.

    Returns:
        The finished runner.

    """
    return subprocess.run(  # noqa: S603 -- Fixed arguments, no shell.
        (sys.executable, "-I", "-m", "baqylau_extension_testkit.runner", str(package)), cwd=package,
        env={"PATH": "/usr/bin:/bin", "BAQYLAU_HOST_EXECUTABLE": str(HOST_EXECUTABLE)},
        capture_output=True, text=True, check=False, timeout=RUN_SECONDS,
    )


def package_with(directory: Path, case: E2eCase) -> Path:
    """Write a web-only package with one declared case file.

    Returns:
        The package directory.

    """
    package = package_fixture.write_package(directory, OWNER, web=True)
    manifest = package_fixture.read_manifest(package)
    package_fixture.save_manifest(package, manifest.model_copy(update={"e2e": (case,)}))
    package_fixture.write_file(package, CASE_PATH, CASES)
    return package


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_live_case_is_reported_apart(tmp_path: Path) -> None:
    """With complete coverage the repeatable case passes and the live case is skipped and counted apart."""
    limit = HarnessLimit(reason="The web view reads no session.")
    case = E2eCase(case_id=WEB, path=CASE_PATH, surfaces=(WEB,), harness_limit=limit)

    finished = runner_output(package_with(tmp_path, case))

    assert finished.returncode == 0, finished.stdout + finished.stderr
    assert "repeatable passed 1, failed 0, skipped 0; live skipped 1" in finished.stdout


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_missing_harness_limit_stops_the_run(tmp_path: Path) -> None:
    """A case without all discovered harnesses and without a limit fails before any case runs."""
    case = E2eCase(case_id=WEB, path=CASE_PATH, surfaces=(WEB,))

    finished = runner_output(package_with(tmp_path, case))

    assert finished.returncode == COVERAGE_FAILED, finished.stdout + finished.stderr
    assert "needs a harness limit with a reason" in finished.stdout
    assert "repeatable passed 0" in finished.stdout
