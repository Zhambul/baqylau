# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the public Claude Code harness plug-in."""

import dataclasses

from audit.recorder import AuditRecorder
from harness.contract import HarnessPlugin, SessionResumeRecorder
from harness.impl.claude_code import definition, plugin_info, plugin_runtime
from harness.impl.claude_code.launcher import ClaudeCodeLauncher
from harness.runtime import HarnessRuntimeConfig
from terminal.contract import TerminalPlugin
from terminal.models.tabs import EnvironmentVariable

MODEL_ALIASES = plugin_info.MODEL_ALIASES
MODEL_IDS = plugin_info.MODEL_IDS
EFFORT_VALUES = plugin_info.EFFORT_VALUES
DEFAULT_MODEL_ID = plugin_info.DEFAULT_MODEL_ID
DEFAULT_EFFORT = plugin_info.DEFAULT_EFFORT
EFFORTS = plugin_info.EFFORTS
MODELS = plugin_info.MODELS
REWIND_MODES = plugin_info.REWIND_MODES
HARNESS_INFO = plugin_info.HARNESS_INFO


def build_plugin(
    harness_runtime_config: HarnessRuntimeConfig,
    terminal_plugin: TerminalPlugin | None = None,
    session_resume_recorder: SessionResumeRecorder | None = None,
    audit_recorder: AuditRecorder | None = None,
    launch_environment: tuple[EnvironmentVariable, ...] = (),
) -> HarnessPlugin:
    """Build the Claude Code plug-in.

    Returns:
        The harness plug-in.

    """
    runtime_plugin = plugin_runtime.runtime_plugin(
        harness_runtime_config,
        HARNESS_INFO,
    )
    if terminal_plugin is None or session_resume_recorder is None or audit_recorder is None:
        return runtime_plugin
    launcher = ClaudeCodeLauncher(
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
