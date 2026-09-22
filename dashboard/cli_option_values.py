# Copyright (c) 2026 Zhambyl Yermagambet
"""Own dashboard option values."""

from types import MappingProxyType

from extensions.configuration import ROOTS_ENVIRONMENT

EXTENSION_ROOT_FLAG = "--extension-root"

LAUNCH_VARIABLES = MappingProxyType({
    "--port": "BAQYLAU_DASHBOARD_PORT",
    "--data-dir": "BAQYLAU_DATA_DIR",
    EXTENSION_ROOT_FLAG: ROOTS_ENVIRONMENT,
})


LOG_FLAG = "--log"


HARNESS_FLAGS = (
    "--harness-executable",
    "--harness-config-dir",
    "--harness-settings-file",
)
