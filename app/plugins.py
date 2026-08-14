"""Discover installed harness descriptors by the plugin-folder contract."""

from __future__ import annotations

import importlib
from pathlib import Path

from contracts.harness import HarnessPlugin


def installed_plugins() -> tuple[HarnessPlugin, ...]:
    plugin_root = Path(__file__).resolve().parents[1] / "plugins"
    descriptors = []
    for descriptor_path in sorted(plugin_root.glob("*/plugin.py")):
        package_name = descriptor_path.parent.name
        module = importlib.import_module(f"plugins.{package_name}.plugin")
        descriptor = getattr(module, "plugin", None)
        if not isinstance(descriptor, HarnessPlugin):
            raise TypeError(f"{module.__name__}.plugin must be a HarnessPlugin")
        descriptors.append(descriptor)
    return tuple(descriptors)
