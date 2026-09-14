# Copyright (c) 2026 Zhambyl Yermagambet
"""Ask Claude Code whether its configured account is signed in."""

import subprocess  # noqa: S404 -- Use the native credential owner.
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from harness.impl.claude_code.usage import live_models
from harness.runtime import HarnessRuntimeConfig


class NativeAuthStatus(BaseModel):
    """Read sign-in state without reading credentials."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    logged_in: bool = Field(alias="loggedIn")


def checked_response(
    probe_result: live_models.ProbeResult,
    harness_runtime_config: HarnessRuntimeConfig,
    environment: Mapping[str, str],
) -> live_models.ProbeResult:
    """Add a sign-in error when native usage is unavailable.

    Returns:
        The usage response or a confirmed sign-in error.

    """
    if probe_result.response is None or probe_result.response.rate_limits_available:
        return probe_result
    if signed_in(harness_runtime_config, environment) is False:
        return live_models.ProbeResult(None, live_models.ProbeFailure(
            "Sign in to Claude Code with claude auth login", recoverable=False, authentication_error=True,
        ))
    return probe_result


def signed_in(harness_runtime_config: HarnessRuntimeConfig, environment: Mapping[str, str]) -> bool | None:
    """Read native sign-in state after an unavailable usage response.

    Returns:
        The sign-in state, or None if the native check fails.

    """
    try:
        response = subprocess.run(  # noqa: S603 -- Fixed native account status operation.
            (harness_runtime_config.executable, "auth", "status", "--json"),
            env=environment, capture_output=True, check=False, timeout=6,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    try:
        return NativeAuthStatus.model_validate_json(response.stdout).logged_in
    except ValidationError:
        return None
