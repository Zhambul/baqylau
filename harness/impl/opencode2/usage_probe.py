# Copyright (c) 2026 Zhambyl Yermagambet
"""Read account limits through the native plugin."""

from harness.impl.opencode2 import native_probe
from harness.impl.opencode2.usage_windows import NativeUsageResponse, NativeUsageResult
from harness.runtime import HarnessRuntimeConfig


def request(harness_runtime_config: HarnessRuntimeConfig) -> NativeUsageResult:
    """Read limits without reading the native credential store.

    Returns:
        The native usage response.

    """
    return NativeUsageResponse.model_validate_json(native_probe.request(harness_runtime_config, "usage")).output
