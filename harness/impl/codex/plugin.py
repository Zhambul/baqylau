# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the public Codex harness plug-in."""

import dataclasses

from audit.recorder import AuditRecorder
from harness.contract import HarnessPlugin, SessionResumeRecorder
from harness.impl.codex import definition, plugin_info, plugin_runtime
from harness.impl.codex.launcher import CodexLauncher
from harness.runtime import HarnessRuntimeConfig
from terminal.contract import TerminalPlugin
from terminal.models.tabs import EnvironmentVariable

LUNA_EFFORTS = plugin_info.LUNA_EFFORTS
MODELS = plugin_info.MODELS
HARNESS_INFO = plugin_info.HARNESS_INFO


def build_plugin(
    harness_runtime_config: HarnessRuntimeConfig,
    terminal_plugin: TerminalPlugin | None = None,
    session_resume_recorder: SessionResumeRecorder | None = None,
    audit_recorder: AuditRecorder | None = None,
    launch_environment: tuple[EnvironmentVariable, ...] = (),
) -> HarnessPlugin:
    """Build the Codex plug-in.

    Returns:
        The harness plug-in.

    """
    runtime_plugin = plugin_runtime.runtime_plugin(
        harness_runtime_config,
        HARNESS_INFO,
    )
    if terminal_plugin is None or session_resume_recorder is None or audit_recorder is None:
        return runtime_plugin
    launcher = CodexLauncher(
        harness_runtime_config,
        terminal_plugin,
        session_resume_recorder,
        audit_recorder,
        launch_environment,
    )
    return dataclasses.replace(runtime_plugin, launcher=launcher)


plugin = build_plugin(
    definition.default_runtime_config(),
)
