# Copyright (c) 2026 Zhambyl Yermagambet
"""Own dashboard forwarding."""

import os
from collections.abc import Mapping

from dashboard.cli_option_values import EXTENSION_ROOT_FLAG, LAUNCH_VARIABLES, LOG_FLAG
from dashboard.cli_options import launch_options


def forwarded_flags(arguments: list[str]) -> list[str]:
    """Build command-line flags for the child dashboard process.

    The same flags, as a child's command line: what `start` hands `serve`.

    Returns:
        Forwarded.

    """
    options = launch_options(arguments)
    flags = _launch_variable_flags(options.variables)
    if options.log_path is not None:
        flags.extend([LOG_FLAG, options.log_path])
    for harness_flag in options.harness_flags:
        flags.extend([harness_flag.name, harness_flag.setting])
    return flags


def _launch_variable_flags(variables: Mapping[str, str]) -> list[str]:
    flags: list[str] = []
    for flag, environment_name in LAUNCH_VARIABLES.items():
        setting = variables.get(environment_name)
        if setting is not None:
            settings = setting.split(os.pathsep) if flag == EXTENSION_ROOT_FLAG else (setting,)
            flags.extend(argument for selected in settings for argument in (flag, selected))
    return flags
