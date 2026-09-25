# Copyright (c) 2026 Zhambyl Yermagambet
"""Run a declared program for a real worker through the host process service."""

from contextlib import closing
from pathlib import Path

from baqylau_extension_api.models.processes import ProcessExited

from tests.extension_host import lifecycle_control_fixture as controls, process_worker_fixture as fixture


def test_worker_runs_a_declared_program(tmp_path: Path, runtime_wheels: Path) -> None:
    """The worker's activation runs `echo` through the host and receives its output."""
    fixture.write_worker(tmp_path, runtime_wheels)
    with closing(controls.open_control(tmp_path)) as case:
        case.control.change_lifecycle(fixture.OWNER, case.request("enable", "enable", fixture.OWNER))
        case.host.finish()

    expected = ProcessExited(name="echo", exit_code=0, stdout="from worker\n", stderr="")
    assert fixture.result(tmp_path) == expected
