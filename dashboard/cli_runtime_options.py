# Copyright (c) 2026 Zhambyl Yermagambet
"""Own dashboard runtime options."""

from dataclasses import replace
from pathlib import Path

from dashboard.cli_models import _HarnessFlag
from dashboard.cli_output import UsageError
from domain.ids import HarnessName
from harness.runtime import HarnessRuntimeConfig, HarnessRuntimeConfigs, default_harness_runtime_configs


def _runtime_configs(harness_flags: list[_HarnessFlag]) -> HarnessRuntimeConfigs:
    configs = default_harness_runtime_configs()
    for flag in harness_flags:
        configs = _updated_runtime(configs, flag)
    return configs


def _updated_runtime(
    harness_runtime_configs: HarnessRuntimeConfigs,
    harness_flag: _HarnessFlag,
) -> HarnessRuntimeConfigs:
    harness_value, configured_value = harness_flag.setting.split("=", 1)
    harness = HarnessName(harness_value)
    try:
        runtime = harness_runtime_configs.for_harness(harness)
    except ValueError as error:
        message = f"unknown harness: {harness_value}"
        raise UsageError(message) from error
    if harness_flag.name == "--harness-executable":
        runtime = replace(runtime, executable=configured_value)
    elif harness_flag.name == "--harness-config-dir":
        runtime = _configuration_runtime(runtime, configured_value)
    elif harness_flag.name == "--harness-settings-file":
        runtime = replace(runtime, settings_file=Path(configured_value))
    return harness_runtime_configs.updated(harness, runtime)


def _configuration_runtime(
    harness_runtime_config: HarnessRuntimeConfig,
    configured_value: str,
) -> HarnessRuntimeConfig:
    configuration_directory = Path(configured_value)
    return replace(
        harness_runtime_config,
        configuration_directory=configuration_directory,
        use_vendor_default_configuration=(
            harness_runtime_config.use_vendor_default_configuration
            and configuration_directory == harness_runtime_config.configuration_directory
        ),
    )
