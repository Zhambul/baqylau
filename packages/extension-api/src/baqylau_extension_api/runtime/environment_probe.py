# Copyright (c) 2026 Zhambyl Yermagambet
"""Report the installed worker environment without importing feature code."""

import platform
import sys
from importlib import metadata
from pathlib import Path

import baqylau_extension_api
from baqylau_extension_api.models.base import NonemptyText, WireModel
from baqylau_extension_api.versions import PackageVersion


class EnvironmentReport(WireModel):
    """Identify the Python environment and installed SDK used for worker preparation."""

    executable: NonemptyText
    prefix: NonemptyText
    base_prefix: NonemptyText
    python_version: PackageVersion
    sdk_version: PackageVersion
    sdk_directory: NonemptyText


def main() -> None:
    """Emit one typed report from the selected private Python process."""
    report = EnvironmentReport(
        executable=sys.executable, prefix=sys.prefix, base_prefix=sys.base_prefix,
        python_version=platform.python_version(), sdk_version=metadata.version("baqylau-extension-api"),
        sdk_directory=str(Path(baqylau_extension_api.__file__).parent),
    )
    sys.stdout.write(report.model_dump_json())


if __name__ == "__main__":
    main()
