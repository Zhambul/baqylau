# Copyright (c) 2026 Zhambyl Yermagambet
"""Build the OpenCode2 part of a real terminal launch."""

from __future__ import annotations

import os
import socket
import subprocess  # noqa: S404 -- Stop only the isolated native test service.
from typing import TYPE_CHECKING

from pydantic import BaseModel

from harness.impl.opencode2 import install

if TYPE_CHECKING:
    from pathlib import Path

    from harness.runtime import HarnessRuntimeConfig
    from sdk.client import SessionRef
    from tests.e2e.testkit.references import SessionSpec

SERVICE_STOP_TIMEOUT_SECONDS = 15


def launch_arguments(resume: SessionRef | None, prompt: str) -> tuple[str, ...]:
    """Return OpenCode2 launch arguments.

    The native TUI reads the installed configuration and uses its shared server.

    Returns:
        OpenCode2 launch arguments.

    """
    arguments: list[str] = []
    if resume is not None:
        arguments.extend(("--session", resume.session_id))
    if prompt.strip():
        # The native side puts this in the draft. It does not send it.
        arguments.extend(("--prompt", prompt))
    return tuple(arguments)


def native_model(spec: SessionSpec) -> str:
    """Return the native model of one session with its effort.

    Returns:
        The native model, with the effort when the case selects one.

    """
    return f"{spec.model}#{spec.effort}" if spec.effort else spec.model


def launch_environment(
    spec: SessionSpec,
    runtime: HarnessRuntimeConfig,
    dashboard_port: int,
) -> dict[str, str]:
    """Install the native configuration used by a normal terminal launch.

    Returns:
        The native configuration names for one launch.

    """
    configuration_home = _configuration_home(runtime)
    native_directory = configuration_home / "opencode"
    install.install(native_directory)
    (native_directory / "opencode.json").write_text(
        TerminalConfiguration(model=native_model(spec)).model_dump_json(),
    )
    ServiceConfiguration.write(native_directory / "service.json")
    environment = {
        "XDG_CONFIG_HOME": str(configuration_home),
        "OPENCODE_CONFIG_DIR": str(native_directory),
        "XDG_STATE_HOME": str(configuration_home / "state"),
        "BAQYLAU_OPENCODE_LOG_DIR": str(runtime.configuration_directory),
        "BAQYLAU_DASHBOARD_PORT": str(dashboard_port),
    }
    if runtime.settings_file is not None:
        environment["OPENCODE_CONFIG"] = str(runtime.settings_file)
    return environment


class TerminalConfiguration(BaseModel):
    """Select the test model through the normal native configuration file."""

    model: str


class ServiceConfiguration(BaseModel):
    """Keep the native test service off the user's service port."""

    hostname: str = "127.0.0.1"
    port: int

    @classmethod
    def write(cls, path: Path) -> None:
        """Set a private service port once for this test profile."""
        if path.exists():
            return
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        path.write_text(cls(port=port).model_dump_json(), encoding="utf-8")


def _configuration_home(runtime: HarnessRuntimeConfig) -> Path:
    return runtime.configuration_directory.parent / "opencode2-terminal-home"


def stop_service(runtime: HarnessRuntimeConfig) -> None:
    """Stop the native service created by a terminal journey."""
    configuration_home = _configuration_home(runtime)
    if not configuration_home.exists():
        return
    subprocess.run(  # noqa: S603 -- Fixed command with an isolated service configuration.
        (runtime.executable, "service", "stop"),
        env={
            **os.environ,
            "XDG_CONFIG_HOME": str(configuration_home),
            "OPENCODE_CONFIG_DIR": str(configuration_home / "opencode"),
            "XDG_STATE_HOME": str(configuration_home / "state"),
        },
        check=True, capture_output=True, timeout=SERVICE_STOP_TIMEOUT_SECONDS,
    )
