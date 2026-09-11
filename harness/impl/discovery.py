# Copyright (c) 2026 Zhambyl Yermagambet
"""Discover installed harness plugins."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from audit.recorder import AuditRecorder
from harness.contract import HarnessPlugin, SessionResumeRecorder
from harness.impl.definitions import definitions
from harness.runtime import HarnessRuntimeConfigs, default_harness_runtime_configs
from terminal.contract import TerminalPlugin
from terminal.models.tabs import EnvironmentVariable

if TYPE_CHECKING:
    from harness.models.definition import HarnessDefinition


@dataclass(frozen=True)
class PluginBuildDependencies:
    """Group the dependencies shared by all discovered plug-ins."""

    runtime_configs: HarnessRuntimeConfigs
    terminal_plugin: TerminalPlugin | None
    session_resume_recorder: SessionResumeRecorder | None
    audit_recorder: AuditRecorder | None
    launch_environment: tuple[EnvironmentVariable, ...]


def installed(
    harness_runtime_configs: HarnessRuntimeConfigs | None = None,
    terminal_plugin: TerminalPlugin | None = None,
    session_resume_recorder: SessionResumeRecorder | None = None,
    audit_recorder: AuditRecorder | None = None,
    launch_environment: tuple[EnvironmentVariable, ...] = (),
) -> tuple[HarnessPlugin, ...]:
    """Return all harness plugins in directory order.

    Returns:
        Installed harness plugins.

    """
    dependencies = PluginBuildDependencies(
        harness_runtime_configs or default_harness_runtime_configs(),
        terminal_plugin,
        session_resume_recorder,
        audit_recorder,
        launch_environment,
    )
    return tuple(
        _installed_plugin(definition, dependencies)
        for definition in definitions()
    )


def _installed_plugin(
    harness_definition: HarnessDefinition,
    plugin_build_dependencies: PluginBuildDependencies,
) -> HarnessPlugin:
    package_name = harness_definition.name
    module = importlib.import_module(f"harness.impl.{package_name}.plugin")
    factory = getattr(module, "build_plugin", None)
    descriptor = (
        factory(
            plugin_build_dependencies.runtime_configs.for_harness(harness_definition.name),
            plugin_build_dependencies.terminal_plugin,
            plugin_build_dependencies.session_resume_recorder,
            plugin_build_dependencies.audit_recorder,
            plugin_build_dependencies.launch_environment,
        )
        if callable(factory)
        else None
    )
    if not isinstance(descriptor, HarnessPlugin):
        message = f"{module.__name__}.build_plugin must return a HarnessPlugin"
        raise TypeError(message)
    if descriptor.harness_info.name != harness_definition.name:
        message = f"{module.__name__} returned a different harness name"
        raise ValueError(message)
    return descriptor
