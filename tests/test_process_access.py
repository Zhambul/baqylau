# Copyright (c) 2026 Zhambyl Yermagambet
"""Run only declared programs, with no shell and with bounded time."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from baqylau_extension_api.errors import ExtensionContractError
from baqylau_extension_api.manifest.contributions import Contributions
from baqylau_extension_api.manifest.processes import ProcessDeclaration
from baqylau_extension_api.models.processes import ProcessExited, ProcessFailed, ProcessRequest, ProcessResult

from extensions.preparation_runner import BoundedPreparationRunner
from extensions.process_access import HostProcessService
from tests.extension_api import manifest_samples

if TYPE_CHECKING:
    from pathlib import Path

ECHO, SLEEP, MISSING = "echo", "sleep", "missing"
SHORT_SECONDS = 0.2
DECLARED = (
    ProcessDeclaration(name=ECHO, executable=ECHO),
    ProcessDeclaration(name=SLEEP, executable=SLEEP, max_seconds=SHORT_SECONDS),
    ProcessDeclaration(name=MISSING, executable="baqylau-no-such-program"),
)
MANIFEST = manifest_samples.backend_manifest("test.processes").model_copy(
    update={"contributions": Contributions(processes=DECLARED)},
)
SERVICE = HostProcessService(MANIFEST, BoundedPreparationRunner())


def run(name: str, arguments: tuple[str, ...], directory: Path) -> ProcessResult:
    """Run one declared fixture program.

    Returns:
        The process result.

    """
    return SERVICE.run_process(ProcessRequest(name=name, arguments=arguments, cwd=str(directory)))


def test_declared_program_runs_without_a_shell(tmp_path: Path) -> None:
    """Arguments reach the program as they are; shell syntax has no effect."""
    expected = ProcessExited(name=ECHO, exit_code=0, stdout="one $HOME; two\n", stderr="")

    assert run(ECHO, ("one", "$HOME;", "two"), tmp_path) == expected


def test_bounds_and_declarations(tmp_path: Path) -> None:
    """A slow program times out, a missing one is not found, and an undeclared one is refused."""
    timed_out = ProcessFailed(name=SLEEP, reason="timed_out")
    not_found = ProcessFailed(name=MISSING, reason="not_found")

    assert run(SLEEP, ("5",), tmp_path) == timed_out
    assert run(MISSING, (), tmp_path) == not_found
    with pytest.raises(ExtensionContractError, match="does not declare"):
        run("rm", ("-rf", str(tmp_path)), tmp_path)
    with pytest.raises(ExtensionContractError, match="does not exist"):
        run(ECHO, (), tmp_path / "absent")
