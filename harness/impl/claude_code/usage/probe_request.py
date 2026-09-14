# Copyright (c) 2026 Zhambyl Yermagambet
"""Send a live usage request through Claude Code."""

import os
import select
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

from harness.impl.claude_code.usage import auth_status, live_models, probe_decode, probe_documents

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness.runtime import HarnessRuntimeConfig

PROBE_TIMEOUT_SECONDS = 6.0
DISCARDED_VARIABLES = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_SUBSCRIPTION_SLUG",
    "CLAUDE_SUBSCRIPTION_LABEL",
)
CONFIG_DIRECTORY_VARIABLE = "CLAUDE_CONFIG_DIR"
PROBE_VARIABLE = "BAQYLAU_USAGE_PROBE"
PIPE_DESCRIPTOR = -1
NULL_DESCRIPTOR = -3


def subprocess_environment(
    harness_runtime_config: "HarnessRuntimeConfig",
) -> "Mapping[str, str]":
    """Build a clean environment for the usage probe.

    Returns:
        The subprocess environment.

    """
    environment = os.environ.copy()
    for name in DISCARDED_VARIABLES:
        environment.pop(name, None)
    if not harness_runtime_config.use_vendor_default_configuration:
        environment[CONFIG_DIRECTORY_VARIABLE] = str(
            harness_runtime_config.configuration_directory,
        )
    environment[PROBE_VARIABLE] = "1"
    return environment


def request_usage(
    harness_runtime_config: "HarnessRuntimeConfig",
) -> live_models.ProbeResult:
    """Request current usage from Claude Code.

    Returns:
        The usage probe result.

    """
    try:
        process = psutil.Popen(
            [
                harness_runtime_config.executable,
                "--print",
                "--verbose",
                "--input-format",
                "stream-json",
                "--output-format",
                "stream-json",
            ],
            stdin=PIPE_DESCRIPTOR,
            stdout=PIPE_DESCRIPTOR,
            stderr=NULL_DESCRIPTOR,
            text=True,
            env=subprocess_environment(harness_runtime_config),
            cwd=Path("~").expanduser(),
            start_new_session=True,
        )
    except FileNotFoundError:
        return live_models.ProbeResult(
            None,
            live_models.ProbeFailure(
                "Claude Code is not installed",
                recoverable=False,
            ),
        )
    except OSError:
        return live_models.ProbeResult(
            None,
            live_models.ProbeFailure(
                "Claude usage probe could not start",
                recoverable=True,
            ),
        )
    try:
        return auth_status.checked_response(
            _exchange_request(process), harness_runtime_config, subprocess_environment(harness_runtime_config),
        )
    except OSError:
        return live_models.ProbeResult(
            None,
            live_models.ProbeFailure(
                "Claude usage probe communication failed",
                recoverable=True,
            ),
        )
    finally:
        _close_process(process)


def _exchange_request(process: psutil.Popen) -> live_models.ProbeResult:
    if process.stdin is None:
        return live_models.ProbeResult(
            None,
            live_models.ProbeFailure(
                "Claude usage probe has no input",
                recoverable=True,
            ),
        )
    request_document = probe_documents.REQUEST.model_dump_json()
    process.stdin.writelines((request_document, "\n"))
    process.stdin.flush()
    return _control_response(process, time.monotonic() + PROBE_TIMEOUT_SECONDS)


def _control_response(process: psutil.Popen, deadline: float) -> live_models.ProbeResult:
    while True:
        line, read_failure = _read_control_line(process, deadline)
        if read_failure is not None:
            return live_models.ProbeResult(None, read_failure)
        decoded = probe_decode.decode_control_line(line or "")
        if decoded is not None:
            return decoded


def _read_control_line(
    process: psutil.Popen,
    deadline: float,
) -> tuple[str | None, live_models.ProbeFailure | None]:
    output = process.stdout
    if output is None:
        return None, live_models.ProbeFailure(
            "Claude usage probe has no output",
            recoverable=True,
        )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, live_models.ProbeFailure(
            "Claude usage probe timed out",
            recoverable=True,
        )
    readable = select.select((output,), (), (), remaining)[0]
    if not readable:
        return None, live_models.ProbeFailure(
            "Claude usage probe timed out",
            recoverable=True,
        )
    line = output.readline()
    if not line:
        return None, live_models.ProbeFailure(
            "Claude usage probe ended early",
            recoverable=True,
        )
    return line, None


def _close_process(process: psutil.Popen) -> None:
    _close_input(process)
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1.0)
    except psutil.TimeoutExpired:
        process.kill()
        process.wait(timeout=1.0)


def _close_input(process: psutil.Popen) -> None:
    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            return
