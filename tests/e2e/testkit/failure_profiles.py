# Copyright (c) 2026 Zhambyl Yermagambet
"""Describe harness profile files for an E2E failure report."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.e2e.testkit import failure_values
from tests.harness_names import CLAUDE_CODE_HARNESS

if TYPE_CHECKING:
    from pathlib import Path

    from harness.runtime import HarnessRuntimeEntry
    from tests.e2e.testkit.process import ApplicationProcess


def runtime_line(entry: HarnessRuntimeEntry) -> str:
    """Describe one configured harness runtime.

    Returns:
        The runtime report line.

    """
    runtime = entry.config
    settings_file = str(runtime.settings_file) if runtime.settings_file else None
    return (
        f"  harness={entry.harness} executable={runtime.executable!r} "
        f"configuration_directory={str(runtime.configuration_directory)!r} settings_file={settings_file!r}"
    )


def claude_profile_line(entry: HarnessRuntimeEntry) -> str | None:
    """Describe the Claude profile file when one is configured.

    Returns:
        The profile report line, or ``None`` for another harness.

    """
    if entry.harness != CLAUDE_CODE_HARNESS:
        return None
    profile = entry.config.configuration_directory / ".claude.json"
    try:
        summary = profile_summary(profile)
    except (OSError, TypeError) as error:
        summary = {"exists": profile.exists(), "read_error": str(error)}
    return f"  profile_state={failure_values.compact(summary)}"


def state(application: ApplicationProcess) -> str:
    """Describe configured harness profiles.

    Returns:
        The profile report section.

    """
    lines = ["harness profiles"]
    for entry in application.config.harness_runtime_configs.entries():
        lines.append(runtime_line(entry))
        profile_line = claude_profile_line(entry)
        if profile_line is not None:
            lines.append(profile_line)
    return "\n".join(lines)


def profile_summary(profile: Path) -> dict[str, failure_values.JsonValue]:
    """Read the selected Claude profile fields.

    Returns:
        The profile field summary.

    Raises:
        TypeError: If the profile content is not a JSON object.

    """
    document = json.loads(profile.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        message = "Claude profile is not a JSON object"
        raise TypeError(message)
    return {
        "exists": True,
        "hasCompletedOnboarding": profile_field(document, "hasCompletedOnboarding"),
        "lastOnboardingVersion": profile_field(document, "lastOnboardingVersion"),
        "theme": profile_field(document, "theme"),
    }


def profile_field(document: dict[object, object], field_name: str) -> failure_values.JsonValue:
    """Return one JSON-compatible profile field.

    Returns:
        The primitive field value or its string representation.

    """
    field_value = document.get(field_name)
    if field_value is None or isinstance(field_value, (bool, float, int, str)):
        return field_value
    return str(field_value)
