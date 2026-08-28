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

from audit.recorder import AuditRecorder
from domain.ids import HarnessName
from harness.contract import HarnessPlugin, SessionResumeRecorder
from harness.runtime import HarnessRuntimeConfigs, default_harness_runtime_configs
from terminal.contract import TerminalPlugin


def installed(
    harness_runtime_configs: HarnessRuntimeConfigs | None = None,
    terminal_plugin: TerminalPlugin | None = None,
    session_resume_recorder: SessionResumeRecorder | None = None,
    audit_recorder: AuditRecorder | None = None,
    launch_environment: tuple[tuple[str, str], ...] = (),
) -> tuple[HarnessPlugin, ...]:
    """Every harness installed here, in directory order."""
    runtime_configs = harness_runtime_configs or default_harness_runtime_configs()
    descriptors = []
    for descriptor_path in sorted(Path(__file__).resolve().parent.glob("*/plugin.py")):
        package_name = descriptor_path.parent.name
        module = importlib.import_module(f"harness.impl.{package_name}.plugin")
        factory = getattr(module, "build_plugin", None)
        descriptor = (
            factory(
                runtime_configs.for_harness(HarnessName(package_name)),
                terminal_plugin,
                session_resume_recorder,
                audit_recorder,
                launch_environment,
            )
            if callable(factory)
            else None
        )
        if not isinstance(descriptor, HarnessPlugin):
            raise TypeError(
                f"{module.__name__}.build_plugin must return a HarnessPlugin"
            )
        descriptors.append(descriptor)
    return tuple(descriptors)
