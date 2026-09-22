# Copyright (c) 2026 Zhambyl Yermagambet
"""Own dashboard option parser."""

import os
from pathlib import Path

from dashboard.cli_models import _HarnessFlag, _ParsedOptions
from dashboard.cli_option_values import EXTENSION_ROOT_FLAG, HARNESS_FLAGS, LAUNCH_VARIABLES, LOG_FLAG
from dashboard.cli_output import UsageError


def _parse_options(arguments: list[str]) -> _ParsedOptions:
    parsed = _ParsedOptions({}, None, [])
    remaining = list(arguments)
    while remaining:
        name, option_content = _next_option(remaining)
        _apply_option(parsed, name, option_content)
    return parsed


def _apply_option(parsed_options: _ParsedOptions, name: str, option_content: str) -> None:
    if name == LOG_FLAG:
        parsed_options.log_path = str(Path(option_content).expanduser().resolve())
    elif name in HARNESS_FLAGS:
        parsed_options.harness_flags.append(_harness_flag(name, option_content))
    elif name == EXTENSION_ROOT_FLAG:
        _apply_extension_root(parsed_options, option_content)
    else:
        _apply_launch_variable(parsed_options, name, option_content)


def _apply_launch_variable(parsed_options: _ParsedOptions, name: str, option_content: str) -> None:
    if name == "--port" and not option_content.isdigit():
        message = f"--port needs a number, not {option_content!r}"
        raise UsageError(message)
    parsed_options.variables[LAUNCH_VARIABLES[name]] = (
        str(Path(option_content).expanduser().resolve()) if name == "--data-dir" else option_content
    )


def _apply_extension_root(parsed_options: _ParsedOptions, option_content: str) -> None:
    path = str(Path(option_content).expanduser().absolute())
    if os.pathsep in path:
        message = "an extension root cannot contain the environment path separator"
        raise UsageError(message)
    environment_name = LAUNCH_VARIABLES[EXTENSION_ROOT_FLAG]
    previous = parsed_options.variables.get(environment_name)
    selected = path if previous is None else os.pathsep.join((previous, path))
    parsed_options.variables[environment_name] = selected


def _next_option(remaining: list[str]) -> tuple[str, str]:
    argument = remaining.pop(0)
    name, inline_content = _split_option(argument)
    option_content = inline_content
    if not option_content and remaining:
        option_content = remaining.pop(0)
    if not option_content:
        message = f"{name} needs a value"
        raise UsageError(message)
    return name, option_content


def _split_option(argument: str) -> tuple[str, str]:
    parts = argument.split("=", 1)
    name = parts[0]
    inline_content = parts[1] if len(parts) > 1 else ""
    if name not in LAUNCH_VARIABLES and name != LOG_FLAG and name not in HARNESS_FLAGS:
        message = f"unknown option: {argument}"
        raise UsageError(message)
    return name, inline_content


def _harness_flag(name: str, option_content: str) -> _HarnessFlag:
    harness_name, separator, harness_value = option_content.partition("=")
    if not separator or not harness_name or not harness_value:
        message = f"{name} needs HARNESS=VALUE"
        raise UsageError(message)
    resolved_value = Path(harness_value).expanduser().resolve()
    return _HarnessFlag(
        name,
        f"{harness_name}={resolved_value}",
    )
