# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the discovered OpenCode2 plugin."""

from dataclasses import replace

from audit.recorder import AuditRecorder
from harness.contract import HarnessPlugin, SessionResumeRecorder
from harness.impl.opencode2 import adapter, definition
from harness.impl.opencode2.catalog import OpenCodeCatalog
from harness.impl.opencode2.launcher import OpenCodeLauncher
from harness.impl.opencode2.usage import OpenCodeUsage
from harness.runtime import HarnessRuntimeConfig
from terminal.contract import TerminalPlugin
from terminal.models.tabs import EnvironmentVariable


def build_plugin(
    harness_runtime_config: HarnessRuntimeConfig,
    terminal_plugin: TerminalPlugin | None = None,
    _session_resume_recorder: SessionResumeRecorder | None = None,
    _audit_recorder: AuditRecorder | None = None,
    launch_environment: tuple[EnvironmentVariable, ...] = (),
) -> HarnessPlugin:
    """Build the native adapter and its terminal launcher.

    Returns:
        The installed plugin.

    """
    descriptor = replace(
        adapter.build_plugin(harness_runtime_config.configuration_directory),
        usage=OpenCodeUsage(harness_runtime_config),
        catalog=OpenCodeCatalog(harness_runtime_config),
    )
    if terminal_plugin is None:
        return descriptor
    return replace(descriptor, launcher=OpenCodeLauncher(
        harness_runtime_config, terminal_plugin, launch_environment,
    ))


plugin = build_plugin(definition.default_runtime_config())
