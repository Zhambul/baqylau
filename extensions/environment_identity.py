# Copyright (c) 2026 Zhambyl Yermagambet
"""Check the actual Python and SDK paths before a worker can use an environment."""

import platform
from importlib import metadata
from pathlib import Path

from baqylau_extension_api.runtime.environment_probe import EnvironmentReport


def validate_environment_identity(executable: Path, report: EnvironmentReport) -> None:
    """Require the host-selected Python and SDK in the selected private environment.

    Raises:
        ValueError: If the report has a different interpreter, SDK, or install path.

    """
    prefix = executable.parent.parent.resolve()
    valid_prefix = (
        prefix == Path(report.prefix).resolve()
        and prefix != Path(report.base_prefix).resolve()
    )
    valid_executable = Path(report.executable).absolute() == executable
    valid_sdk = (report.sdk_version, report.python_version) == (
        metadata.version("baqylau-extension-api"), platform.python_version(),
    )
    if not valid_prefix or not valid_executable or not valid_sdk:
        message = "the prepared Python or SDK identity does not match the host selection"
        raise ValueError(message)
    if not Path(report.sdk_directory).resolve().is_relative_to(prefix):
        message = "the worker SDK is outside its private environment"
        raise ValueError(message)
