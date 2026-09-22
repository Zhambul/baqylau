# Copyright (c) 2026 Zhambyl Yermagambet
"""Check owned process-group cleanup after normal and failed preparation."""

from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import psutil
import pytest

from extensions.preparation_output import PreparationOutputLimitError
from extensions.preparation_runner import BoundedPreparationRunner
from tests.extension_host.preparation_fixture import command

SOURCE = Path(__file__).with_name("fixtures") / "preparation_children.py.txt"


@pytest.mark.parametrize("mode", ["success", "timeout", "flood"])
def test_preparation_releases_owned_descendants(tmp_path: Path, mode: str) -> None:
    """A child with closed output pipes cannot survive its preparation operation."""
    selected = command(tmp_path, SOURCE.read_text(encoding="utf-8"), timeout=2)
    operation = replace(selected, arguments=(*selected.arguments, mode))
    expected = {"timeout": TimeoutError, "flood": PreparationOutputLimitError}.get(mode)
    with nullcontext() if expected is None else pytest.raises(expected):
        BoundedPreparationRunner().run_preparation(operation)
    _wait_for_child_exit(int((tmp_path / "owned-child").read_text(encoding="utf-8")))


def _wait_for_child_exit(pid: int) -> None:
    try:
        process = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    process.wait(timeout=5)
    assert not psutil.pid_exists(pid)
