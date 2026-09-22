# Copyright (c) 2026 Zhambyl Yermagambet
"""Run installed tools in the selected project with the shared rule policy."""

import subprocess  # noqa: S404 -- Run fixed installed quality tools without a shell.
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from baqylau_dev.commands import LINT_GATES, tool_arguments
from baqylau_dev.deadcode import check_deadcode
from baqylau_dev.extension_checks import check_extension
from baqylau_dev.models import Gate, ProjectProfile
from baqylau_dev.parity import check_parity, write_configuration
from baqylau_dev.profiles import load_profile

if TYPE_CHECKING:
    from baqylau_dev.class_declarations import CodeLocation

DEADCODE: Gate = "deadcode"


def run_gate(root: Path, gate: Gate) -> int:
    """Reject drift, prepare external configuration, and run the selected gate.

    Returns:
        Zero only if every required command passes.

    """
    root = root.resolve()
    profile = load_profile(root)
    check_parity(root, profile)
    if gate == "parity":
        return 0
    entries: frozenset[CodeLocation] = frozenset()
    if profile.profile == "extension":
        write_configuration(root, profile)
        if gate in {"architecture", "types", DEADCODE, "lint"}:
            entries = check_extension(root, profile)
    for selected in _selected_gates(profile, gate):
        if profile.profile == "extension" and selected == DEADCODE:
            checked = check_deadcode(root, profile, entries)
        else:
            checked = _run_tool(root, profile, selected)
        if checked:
            return checked
    return 0


def _selected_gates(profile: ProjectProfile, gate: Gate) -> tuple[Gate, ...]:
    if gate == "lint":
        return LINT_GATES
    if profile.profile == "extension":
        if gate == "architecture":
            return ("types",)
        if gate == DEADCODE:
            return ("types", DEADCODE)
    return (gate,)


def _run_tool(root: Path, profile: ProjectProfile, gate: Gate) -> int:
    completed = subprocess.run(  # noqa: S603 -- Fixed Python modules and validated local input paths; no shell.
        (sys.executable, "-m", *tool_arguments(root, profile, gate)), cwd=root, check=False,
    )
    return completed.returncode
