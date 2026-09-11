# Copyright (c) 2026 Zhambyl Yermagambet
"""Build isolated commands and environments for model providers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from domain.ids import HarnessName
from terminal.models.tabs import EnvironmentVariable

if TYPE_CHECKING:

    from harness.runtime import HarnessRuntimeConfig

INTERNAL_MODEL_VARIABLE = "BAQYLAU_INTERNAL_MODEL"
TITLE_SCHEMA_JSON = (
    '{"type":"object","properties":{"title":{"type":"string"}},"required":["title"],"additionalProperties":false}'
)


@dataclass(frozen=True)
class ProviderCandidate:
    """Describe one command-line model provider."""

    harness: HarnessName
    executable: str
    command: Callable[[str, str], tuple[str, ...]]


def codex_command(prompt: str, schema_path: str) -> tuple[str, ...]:
    """Build an isolated Codex command.

    Returns:
        Result items.

    """
    arguments = [
        "codex",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--model",
        "gpt-5.6-luna",
    ]
    arguments.extend(
        (
            "--config",
            'model_reasoning_effort="low"',
            "--sandbox",
            "read-only",
            "--output-schema",
            schema_path,
            "--color",
            "never",
            "--json",
            prompt,
        ),
    )
    return tuple(arguments)


def claude_command(prompt: str, _schema_path: str) -> tuple[str, ...]:
    """Build an isolated Claude command.

    Returns:
        Result items.

    """
    arguments = [
        "claude",
        "--print",
        "--safe-mode",
        "--no-session-persistence",
        "--tools",
        "",
        "--model",
        "haiku",
    ]
    arguments.extend(
        (
            "--effort",
            "low",
            "--output-format",
            "json",
            "--json-schema",
            TITLE_SCHEMA_JSON,
            prompt,
        ),
    )
    return tuple(arguments)


def model_environment(
    harness: HarnessName,
    runtime_config: HarnessRuntimeConfig,
) -> tuple[EnvironmentVariable, ...]:
    """Build the isolated environment for one provider.

    Returns:
        Result items.

    """
    if harness == HarnessName("claude_code"):
        return _claude_environment(runtime_config)
    return (
        EnvironmentVariable(INTERNAL_MODEL_VARIABLE, "1"),
        EnvironmentVariable(
            "CODEX_HOME",
            str(runtime_config.configuration_directory),
        ),
    )


def _claude_environment(
    runtime_config: HarnessRuntimeConfig,
) -> tuple[EnvironmentVariable, ...]:
    environment = [EnvironmentVariable(INTERNAL_MODEL_VARIABLE, "1")]
    if not runtime_config.use_vendor_default_configuration:
        environment.append(
            EnvironmentVariable(
                "CLAUDE_CONFIG_DIR",
                str(runtime_config.configuration_directory),
            ),
        )
    if runtime_config.settings_file is not None:
        environment.append(
            EnvironmentVariable(
                "CLAUDE_CODE_MANAGED_SETTINGS_PATH",
                str(runtime_config.settings_file),
            ),
        )
    return tuple(environment)


PROVIDER_CANDIDATES = (
    ProviderCandidate(HarnessName("codex"), "codex", codex_command),
    ProviderCandidate(HarnessName("claude_code"), "claude", claude_command),
)
