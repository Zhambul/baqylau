# Copyright (c) 2026 Zhambyl Yermagambet
"""Expose policy checks and generation through one installed command."""

import argparse
import sys
from pathlib import Path
from typing import get_args

from pydantic import TypeAdapter

from baqylau_dev.models import Gate
from baqylau_dev.parity import write_configuration
from baqylau_dev.profiles import load_profile
from baqylau_dev.resources import policy_digest, policy_version, require_tool_versions, tool_versions
from baqylau_dev.runner import run_gate


def main() -> int:
    """Parse a narrow fixed command interface.

    Returns:
        A tool exit code, or two for a policy or path error.

    """
    parser = argparse.ArgumentParser(description="Use the shared Baqylau Python quality rules.")
    parser.add_argument("command", choices=("check", "generate", "report"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--gate", choices=get_args(Gate.__value__), default="lint")
    arguments = parser.parse_args()
    try:
        return _execute(
            arguments.command, arguments.root.resolve(), TypeAdapter(Gate).validate_python(arguments.gate),
        )
    except (OSError, ValueError) as error:
        sys.stderr.write(f"Policy error: {error}\n")
        return 2


def _execute(command: str, root: Path, gate: Gate) -> int:
    if command == "report":
        return _report()
    if command == "generate":
        require_tool_versions()
        write_configuration(root, load_profile(root))
        return 0
    return run_gate(root, gate)


def _report() -> int:
    sys.stdout.write(f"baqylau-dev {policy_version()}\nPolicy SHA-256: {policy_digest()}\n")
    for tool in tool_versions():
        sys.stdout.write(f"{tool.name}: required {tool.required}; installed {tool.installed}\n")
    require_tool_versions()
    return 0
