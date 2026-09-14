# Copyright (c) 2026 Zhambyl Yermagambet
"""Encode native plugin launch settings."""

from pathlib import Path

from pydantic import BaseModel, Field

HOOK_PATH = "/api/harnesses/opencode2/hooks"


class PluginOptions(BaseModel):
    """Use the native plugin option names."""

    log_directory: str = Field(serialization_alias="logDirectory")
    endpoint: str


class PluginPackage(BaseModel):
    """Select the native plugin package."""

    package: str
    options: PluginOptions


class LaunchConfiguration(BaseModel):
    """Set the model and event plugin for one native process."""

    model: str
    plugins: tuple[PluginPackage, ...]
    snapshots: bool = True

    def encode(self) -> str:
        """Encode the configuration.

        Returns:
            Native configuration JSON.

        """
        return self.model_dump_json(by_alias=True)


def session_configuration(model: str, log_directory: str, port: str) -> str:
    """Encode the settings that one native session starts with.

    Every session needs the same event plugin, so a launch from the dashboard
    and a launch from a terminal build this in one place.

    Returns:
        Native configuration JSON.

    """
    return LaunchConfiguration(model=model, plugins=(PluginPackage(
        package=Path(__file__).parent.as_uri(),
        options=PluginOptions(
            log_directory=log_directory,
            endpoint=f"http://127.0.0.1:{port}{HOOK_PATH}",
        ),
    ),)).encode()
