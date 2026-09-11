# Copyright (c) 2026 Zhambyl Yermagambet
"""Read plugin declarations from the installed plugin directories."""

import importlib
from pathlib import Path

from harness.models.definition import HarnessDefinition

PLUGIN_DIRECTORY = Path(__file__).resolve().parent


def definitions() -> tuple[HarnessDefinition, ...]:
    """Read declarations in a stable order.

    Returns:
        Validated declarations for all installed plugins.

    """
    return tuple(_definition(path) for path in sorted(PLUGIN_DIRECTORY.glob("*/plugin.py")))


def _definition(path: Path) -> HarnessDefinition:
    package_name = path.parent.name
    module = importlib.import_module(f"harness.impl.{package_name}.definition")
    declaration = getattr(module, "DEFINITION", None)
    if not isinstance(declaration, HarnessDefinition) or declaration.name != path.parent.name:
        message = f"{module.__name__}.DEFINITION must declare the plugin directory name"
        raise TypeError(message)
    return declaration
