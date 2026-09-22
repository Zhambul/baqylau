# Copyright (c) 2026 Zhambyl Yermagambet
"""Check real command bounds, stream drainage, and process-group ownership."""

from dataclasses import replace
from pathlib import Path

import pytest

from extensions.preparation_output import PreparationOutputLimitError
from extensions.preparation_runner import BoundedPreparationRunner
from tests.extension_host import preparation_fixture as preparation

FAILURE_CODE = 7
INVALID_TIMEOUTS = (0, -1, float("nan"), float("inf"))


def test_runner_reads_both_streams(tmp_path: Path) -> None:
    """Stderr cannot hold a process whose stdout is also being collected."""
    command = preparation.command(tmp_path, "import os; os.write(1,b'out'); os.write(2,b'err')")
    output = BoundedPreparationRunner().run_preparation(command)
    assert output.return_code == 0
    assert output.stdout == b"out"
    assert output.stderr == b"err"


def test_runner_reports_failure_code(tmp_path: Path) -> None:
    """The caller can distinguish a child failure from a successful empty result."""
    command = preparation.command(tmp_path, "raise SystemExit(7)")
    output = BoundedPreparationRunner().run_preparation(command)
    assert output.return_code == FAILURE_CODE
    assert not output.stdout


def test_runner_enforces_timeout(tmp_path: Path) -> None:
    """A sleeping preparation process cannot exceed its selected deadline."""
    command = preparation.command(tmp_path, "import time; time.sleep(60)", timeout=0.1)
    with pytest.raises(TimeoutError):
        BoundedPreparationRunner().run_preparation(command)


def test_runner_enforces_combined_output(tmp_path: Path) -> None:
    """Both output channels count toward one budget."""
    command = preparation.command(tmp_path, "import os; os.write(1,b'a'*8); os.write(2,b'b'*8)", limit=10)
    with pytest.raises(PreparationOutputLimitError):
        BoundedPreparationRunner().run_preparation(command)


@pytest.mark.parametrize("timeout", INVALID_TIMEOUTS)
def test_runner_rejects_invalid_timeout(tmp_path: Path, timeout: float) -> None:
    """Invalid limits fail before a program can start."""
    command = preparation.command(tmp_path, "raise AssertionError('not started')", timeout=timeout)
    with pytest.raises(ValueError, match="positive finite"):
        BoundedPreparationRunner().run_preparation(command)


def test_runner_rejects_empty_command(tmp_path: Path) -> None:
    """No shell or environment fallback can supply a missing program."""
    command = preparation.command(tmp_path, "pass")
    with pytest.raises(ValueError, match="explicit command"):
        BoundedPreparationRunner().run_preparation(replace(command, arguments=()))
