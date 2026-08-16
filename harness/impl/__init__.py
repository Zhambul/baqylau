# harness/impl/ — the concrete harnesses, and the one function that finds them.
#
# A harness is ONE directory here whose `plugin.py` exports a `HarnessPlugin`;
# adding a third needs no edit above this package, and no registry list to keep
# in sync — the folder IS the registration.
#
# `installed()` is the only import boundary crossing out of here: everything
# else in the tree takes a `HarnessPlugin` (or one of its fields) by injection,
# so `harness.impl` has exactly one importer — bootstrap — plus the hook entry
# processes, which run inside a harness's own process tree and import their own
# package directly.
from __future__ import annotations

import importlib
from pathlib import Path

from harness.contract import HarnessPlugin


def installed() -> tuple[HarnessPlugin, ...]:
    """Every harness installed here, in directory order."""
    descriptors = []
    for descriptor_path in sorted(Path(__file__).resolve().parent.glob("*/plugin.py")):
        package_name = descriptor_path.parent.name
        module = importlib.import_module(f"harness.impl.{package_name}.plugin")
        descriptor = getattr(module, "plugin", None)
        if not isinstance(descriptor, HarnessPlugin):
            raise TypeError(f"{module.__name__}.plugin must be a HarnessPlugin")
        descriptors.append(descriptor)
    return tuple(descriptors)
