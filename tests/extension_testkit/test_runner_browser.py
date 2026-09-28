# Copyright (c) 2026 Zhambyl Yermagambet
"""A package's own browser case runs through the installed runner against a private host (P08-T02)."""

from __future__ import annotations

import os
import subprocess  # noqa: S404 -- Run the installed runner module without a shell.
import sys
from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.manifest.e2e import E2eCase, HarnessLimit

from tests import host_launcher
from tests.extension_host import package_fixture
from tests.extension_web import view_server

if TYPE_CHECKING:
    from pathlib import Path

CASE_PATH = "tests/e2e/test_web.py"
LIMIT = HarnessLimit(reason="The workspace page reads no session.")
RUN_SECONDS = 240
TEST_TIMEOUT_SECONDS = 300
BROWSER_CASE = f"""import shutil
from pathlib import Path

from baqylau_extension_testkit.browser import extension_view, workspace_view_url
from baqylau_extension_testkit.lifecycle import change
from playwright.sync_api import expect, sync_playwright

OWNER = "{view_server.OWNER}"
VIEW = f"{{OWNER}}.main"


def test_workspace_view(baqylau_host):
    shutil.copytree(Path(__file__).resolve().parents[2], baqylau_host.roots.packages / OWNER)
    client = baqylau_host.start()
    assert change(client, OWNER, "enable", "enable").status == "succeeded"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(workspace_view_url(str(client.transport.base_url).rstrip("/"), "workspace-one", OWNER, VIEW))
        expect(extension_view(page, VIEW)).to_contain_text(f"Hello from {{OWNER}} in workspace")
        browser.close()
""".encode()


def browser_package(directory: Path) -> Path:
    """Write the view package with one declared browser case.

    Returns:
        The package directory.

    """
    view_server.write_view_package(directory)
    package = directory / view_server.OWNER
    manifest = package_fixture.read_manifest(package)
    case = E2eCase(case_id="web", path=CASE_PATH, surfaces=("web",), harness_limit=LIMIT)
    package_fixture.save_manifest(package, manifest.model_copy(update={"e2e": (case,)}))
    package_fixture.write_file(package, CASE_PATH, BROWSER_CASE)
    return package


@pytest.mark.timeout(TEST_TIMEOUT_SECONDS)
def test_browser_case_runs_through_the_runner(tmp_path: Path) -> None:
    """The runner checks coverage, then the package's browser case mounts its view in headless Chromium."""
    package = browser_package(tmp_path)
    environment = {
        "PATH": os.defpath, "BAQYLAU_HOST_EXECUTABLE": str(host_launcher.host_executable()),
        # Playwright finds its installed browsers under the caller's home.
        "HOME": os.environ["HOME"],
    }

    finished = subprocess.run(  # noqa: S603 -- Fixed arguments, no shell.
        (sys.executable, "-I", "-m", "baqylau_extension_testkit.runner", str(package)), cwd=package,
        env=environment, capture_output=True, text=True, check=False, timeout=RUN_SECONDS,
    )

    assert finished.returncode == 0, finished.stdout + finished.stderr
    assert "repeatable passed 1, failed 0" in finished.stdout
