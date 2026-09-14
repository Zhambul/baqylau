# Copyright (c) 2026 Zhambyl Yermagambet
"""Build real-harness launch commands for E2E journeys."""

from __future__ import annotations

import shlex
from itertools import starmap
from typing import TYPE_CHECKING

from domain.ids import HarnessName
from terminal.models.tabs import EnvironmentVariable
from tests.e2e.testkit import journey_opencode, journey_session_ids
from tests.harness_names import CLAUDE_CODE_HARNESS, OPENCODE_HARNESS

if TYPE_CHECKING:
    from harness.runtime import HarnessRuntimeConfig
    from sdk.client import SessionRef
    from tests.e2e.testkit.references import SessionSpec


def reusable_shell_command(command: tuple[str, ...]) -> tuple[str, ...]:
    """Return a shell command that keeps the terminal open.

    Returns:
        A shell command that keeps the terminal open.

    """
    invocation = shlex.join(command)
    return ("/bin/zsh", "-fc", f"{invocation}; exec /bin/zsh -fi")


def unattended_session_id(harness: str, output: str) -> str:
    """Return the native unattended session id.

    Returns:
        The native session id.

    """
    return journey_session_ids.unattended_session_id(harness, output)


def launch_arguments(
    harness: HarnessName,
    spec: SessionSpec,
    resume: SessionRef | None,
    workspace: str,
    prompt: str,
) -> tuple[str, ...]:
    """Return the command arguments for a harness launch.

    Returns:
        The command arguments for a harness launch.

    """
    if harness == OPENCODE_HARNESS:
        return journey_opencode.launch_arguments(resume, prompt)
    arguments = (
        claude_launch_arguments(spec, resume)
        if harness == CLAUDE_CODE_HARNESS
        else codex_launch_arguments(spec, resume, workspace)
    )
    return (*arguments, prompt) if prompt.strip() else arguments


def claude_launch_arguments(spec: SessionSpec, resume: SessionRef | None) -> tuple[str, ...]:
    """Return Claude Code launch arguments.

    Returns:
        Claude Code launch arguments.

    """
    arguments: list[str] = []
    if resume is not None:
        arguments.extend(("--resume", resume.session_id))
    if spec.model:
        arguments.extend(("--model", spec.model))
    if spec.effort:
        arguments.extend(("--effort", spec.effort))
    return tuple(arguments)


def codex_launch_arguments(spec: SessionSpec, resume: SessionRef | None, workspace: str) -> tuple[str, ...]:
    """Return Codex launch arguments.

    Returns:
        Codex launch arguments.

    """
    arguments: list[str] = []
    if resume is not None:
        arguments.extend(("resume", resume.session_id))
    arguments.extend(("-C", spec.workspace or workspace))
    if spec.model:
        arguments.extend(("-m", spec.model))
    if spec.effort:
        arguments.extend(("-c", f"model_reasoning_effort={spec.effort}"))
    arguments.extend(("-c", 'model_reasoning_summary="concise"'))
    return tuple(arguments)


def launch_environment(
    spec: SessionSpec,
    runtime: HarnessRuntimeConfig,
    dashboard_port: int,
    environment_values: tuple[EnvironmentVariable, ...],
) -> tuple[EnvironmentVariable, ...]:
    """Return environment variables for a harness launch.

    Returns:
        Environment variables for a harness launch.

    """
    harness = HarnessName(spec.harness)
    environment = {launch_value.name: launch_value.content for launch_value in environment_values}
    environment["BAQYLAU_DASHBOARD_PORT"] = str(dashboard_port)
    if harness == CLAUDE_CODE_HARNESS:
        environment["CLAUDE_CONFIG_DIR"] = str(runtime.configuration_directory)
        if runtime.settings_file is not None:
            environment["CLAUDE_CODE_MANAGED_SETTINGS_PATH"] = str(runtime.settings_file)
    elif harness == OPENCODE_HARNESS:
        environment.update(journey_opencode.launch_environment(spec, runtime, dashboard_port))
    else:
        environment["CODEX_HOME"] = str(runtime.configuration_directory)
    return tuple(starmap(EnvironmentVariable, environment.items()))
